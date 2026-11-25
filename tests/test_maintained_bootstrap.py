"""Tests for the maintained-database bootstrap and auto-update service."""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from web.backend.services.maintained_bootstrap import (
    _db_path_for_name,
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
    buf = io.BytesIO(body)
    resp = MagicMock(read=MagicMock(side_effect=buf.read))

    def _enter(*_args: Any) -> MagicMock:
        buf.seek(0)
        return resp

    mock = MagicMock()
    mock.__enter__ = MagicMock(side_effect=_enter)
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
        assert init_calls[-1].parent != tmp_path
        assert init_calls[-1].parent.parent == tmp_path
        assert init_calls[-1].parent.name.startswith('.alpha_db.db.')
        assert not init_calls[-1].exists()

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
        from web.backend.background import start_maintained_db_update_thread

        app_state = MagicMock()
        with patch('web.backend.background._maintained_db_update_loop') as loop_mock:
            start_maintained_db_update_thread(tmp_path, 0, app_state)
        time.sleep(0.02)
        assert loop_mock.call_count == 0

    def test_starts_thread_and_calls_check_when_interval_positive(self, tmp_path: Path) -> None:
        from web.backend.background import start_maintained_db_update_thread

        updated = threading.Event()

        class _AppState:
            def __init__(self) -> None:
                self._project_db_uuid_index: dict[str, Path] = {}

            @property
            def project_db_uuid_index(self) -> dict[str, Path]:
                return self._project_db_uuid_index

            @project_db_uuid_index.setter
            def project_db_uuid_index(self, value: dict[str, Path]) -> None:
                self._project_db_uuid_index = value
                updated.set()

        app_state = _AppState()
        stop_event = threading.Event()
        thread = None
        try:
            with (
                patch('web.backend.background.check_and_update_maintained_databases') as check_mock,
                patch('web.backend.background.build_project_db_uuid_index', return_value={'u': tmp_path / 'x.db'}),
            ):
                thread = start_maintained_db_update_thread(tmp_path, 0.01, app_state, stop_event=stop_event)
                assert updated.wait(timeout=2)
                assert check_mock.call_count >= 1
            assert app_state.project_db_uuid_index == {'u': tmp_path / 'x.db'}
        finally:
            stop_event.set()
            if thread is not None:
                thread.join(timeout=2)

    def test_interval_resolver_reads_env_and_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from web.backend.background import resolve_maintained_db_update_interval

        default = 604800
        monkeypatch.delenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', raising=False)
        assert resolve_maintained_db_update_interval() == default
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', '123')
        assert resolve_maintained_db_update_interval() == 123
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', '0')
        assert resolve_maintained_db_update_interval() == 0

    def test_interval_resolver_falls_back_on_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from web.backend.background import resolve_maintained_db_update_interval

        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', 'not-a-number')
        assert resolve_maintained_db_update_interval() == 604800
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', '-5')
        assert resolve_maintained_db_update_interval() == 604800


# ── stale UUID index refresh ───────────────────────────────────────────────────

class TestStaleUuidIndexRefresh:
    def test_weekly_loop_clears_and_updates_app_state_index(self, tmp_path: Path) -> None:
        from web.backend.background import start_maintained_db_update_thread

        # Atomic rebinding: app_state.project_db_uuid_index is replaced with a new dict
        # rather than mutated via .clear()/.update(), leaving stale snapshots intact.
        stale = {'old-uuid': tmp_path / 'old.db'}
        refreshed = {'new-uuid': tmp_path / 'new.db'}
        rebound = threading.Event()

        class _AppState:
            def __init__(self) -> None:
                self._project_db_uuid_index = stale

            @property
            def project_db_uuid_index(self) -> dict[str, Path]:
                return self._project_db_uuid_index

            @project_db_uuid_index.setter
            def project_db_uuid_index(self, value: dict[str, Path]) -> None:
                self._project_db_uuid_index = value
                rebound.set()

        app_state = _AppState()
        stop_event = threading.Event()
        thread = None
        try:
            with (
                patch('web.backend.background.check_and_update_maintained_databases'),
                patch('web.backend.background.build_project_db_uuid_index', return_value=refreshed),
            ):
                thread = start_maintained_db_update_thread(tmp_path, 0.01, app_state, stop_event=stop_event)
                assert rebound.wait(timeout=2)

            assert app_state.project_db_uuid_index is not stale
            assert app_state.project_db_uuid_index == refreshed
            assert stale == {'old-uuid': tmp_path / 'old.db'}
        finally:
            stop_event.set()
            if thread is not None:
                thread.join(timeout=2)


# ── S3 / WA-018 / WA-031 / WA-040 hardening tests ─────────────────────────────

class TestMaintainedBootstrapSecurityAndConcurrency:
    @pytest.mark.parametrize('bad_name', ['../escape', 'a/b', 'a\\b', '.', '..', '.hidden', 'bad name'])
    def test_db_path_for_name_rejects_unsafe_names(self, tmp_path: Path, bad_name: str) -> None:
        with pytest.raises(ValueError):
            _db_path_for_name(tmp_path, bad_name)

    def test_bootstrap_rejects_unsafe_name_before_any_download(self, tmp_path: Path) -> None:
        with (
            patch('web.backend.services.maintained_bootstrap.list_maintained_databases', return_value=['../evil', 'ok_db']),
            patch('web.backend.services.maintained_bootstrap.download_database_files') as dl_mock,
        ):
            with pytest.raises(ValueError):
                bootstrap_missing_maintained_databases(tmp_path)
        dl_mock.assert_not_called()
        assert not (tmp_path.parent / 'evil.db').exists()

    def test_bootstrap_and_update_refuse_symlink_targets_and_sidecars(self, tmp_path: Path) -> None:
        outside = tmp_path / 'outside.db'
        _make_project_db(outside, name='sym_db', tsv_checksum='sha256:old')
        pd_dir = tmp_path / 'pds'
        pd_dir.mkdir()
        sym_db = pd_dir / 'sym_db.db'
        sym_db.symlink_to(outside)

        with (
            patch('web.backend.services.maintained_bootstrap.list_maintained_databases', return_value=['sym_db']),
            patch('web.backend.services.maintained_bootstrap.download_database_files') as dl_mock,
        ):
            with pytest.raises(ValueError, match='symlink'):
                bootstrap_missing_maintained_databases(pd_dir)
        dl_mock.assert_not_called()
        assert sym_db.is_symlink()
        assert _read_local_tsv_checksum(outside) == 'sha256:old'

        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases_with_checksums',
                return_value=[('sym_db', 'sha256:new')],
            ),
            patch('web.backend.services.maintained_bootstrap.download_database_files') as dl_mock2,
        ):
            check_and_update_maintained_databases(pd_dir)
        dl_mock2.assert_not_called()
        assert sym_db.is_symlink()
        assert _read_local_tsv_checksum(outside) == 'sha256:old'

        # Symlinked -wal sidecar or lockfile also rejected
        real_db = _make_project_db(pd_dir / 'real_db.db', name='real_db', tsv_checksum='sha256:old')
        (pd_dir / 'real_db.db-wal').symlink_to(tmp_path / 'outside-wal')
        with pytest.raises(ValueError, match='symlink'):
            _db_path_for_name(pd_dir, 'real_db')
        assert real_db.is_file()

    def test_bootstrap_does_not_unlink_invalid_stub_if_rebuild_fails(self, tmp_path: Path) -> None:
        stub = tmp_path / 'alpha_db.db'
        stub.write_bytes(b'incomplete-stub')
        with (
            patch('web.backend.services.maintained_bootstrap.list_maintained_databases', return_value=['alpha_db']),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
                side_effect=RuntimeError('network down'),
            ),
        ):
            with pytest.raises(RuntimeError, match='network down'):
                bootstrap_missing_maintained_databases(tmp_path)
        assert stub.is_file()
        assert stub.read_bytes() == b'incomplete-stub'

    def test_bootstrap_isolates_per_db_failure_so_remaining_dbs_succeed(self, tmp_path: Path) -> None:
        def dl_side_effect(name: str, dest_dir: Path) -> dict:
            if name == 'alpha_db':
                raise RuntimeError('alpha failed')
            return _make_files(dest_dir)

        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases',
                return_value=['alpha_db', 'beta_db'],
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
                side_effect=dl_side_effect,
            ),
            patch(
                'web.backend.services.maintained_bootstrap.init_project',
                side_effect=_install_init_checksum_writer('sha256:beta'),
            ),
        ):
            with pytest.raises(RuntimeError, match='alpha failed'):
                bootstrap_missing_maintained_databases(tmp_path)

        assert not (tmp_path / 'alpha_db.db').exists()
        assert (tmp_path / 'beta_db.db').is_file()
        assert _read_local_tsv_checksum(tmp_path / 'beta_db.db') == 'sha256:beta'

    def test_rebuild_switches_to_delete_journal_mode_and_cleans_wal_sidecars(self, tmp_path: Path) -> None:
        dest = _make_project_db(tmp_path / 'alpha_db.db', name='alpha_db', tsv_checksum='sha256:old')
        Path(f'{dest}-wal').write_bytes(b'stale-wal')
        Path(f'{dest}-shm').write_bytes(b'stale-shm')

        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases_with_checksums',
                return_value=[('alpha_db', 'sha256:new')],
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
                side_effect=lambda _name, d: _make_files(d),
            ),
            patch(
                'web.backend.services.maintained_bootstrap.init_project',
                side_effect=_install_init_checksum_writer('sha256:new'),
            ),
        ):
            check_and_update_maintained_databases(tmp_path)

        assert not Path(f'{dest}-wal').exists()
        assert not Path(f'{dest}-shm').exists()
        conn = sqlite3.connect(dest)
        try:
            mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
        finally:
            conn.close()
        assert str(mode).lower() == 'delete'

    def test_concurrent_bootstrap_serialized_by_flock_rebuilds_only_once(self, tmp_path: Path) -> None:
        build_count = 0
        count_lock = threading.Lock()
        entered_first = threading.Event()
        release_first = threading.Event()

        def slow_init(*, db_path, name, genbank_paths, rules_tsv, formula_rules_tsv, metadata_json,
                      overwrite=False, additional_info=True, example_fasta=None):
            nonlocal build_count
            with count_lock:
                build_count += 1
                current = build_count
            if current == 1:
                entered_first.set()
                release_first.wait(timeout=5)
            return _install_init_checksum_writer('sha256:v1')(
                db_path=db_path,
                name=name,
                genbank_paths=genbank_paths,
                rules_tsv=rules_tsv,
                formula_rules_tsv=formula_rules_tsv,
                metadata_json=metadata_json,
                overwrite=overwrite,
                additional_info=additional_info,
                example_fasta=example_fasta,
            )

        errors: list[Exception] = []

        def run_boot() -> None:
            try:
                bootstrap_missing_maintained_databases(tmp_path)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with (
            patch('web.backend.services.maintained_bootstrap.list_maintained_databases', return_value=['alpha_db']),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
                side_effect=lambda _name, d: _make_files(d),
            ),
            patch('web.backend.services.maintained_bootstrap.init_project', side_effect=slow_init),
        ):
            t1 = threading.Thread(target=run_boot)
            t2 = threading.Thread(target=run_boot)
            t1.start()
            assert entered_first.wait(timeout=3)
            t2.start()
            time.sleep(0.05)
            release_first.set()
            t1.join(timeout=5)
            t2.join(timeout=5)

        assert errors == []
        assert build_count == 1
        assert (tmp_path / 'alpha_db.db').is_file()


class TestBackgroundAndStartupPolicyHardening:
    def test_update_thread_waits_before_first_tick_and_exits_on_stop(self, tmp_path: Path) -> None:
        from web.backend.background import start_maintained_db_update_thread

        app_state = MagicMock()
        app_state.project_db_uuid_index = {}
        stop_event = threading.Event()
        with patch('web.backend.background.check_and_update_maintained_databases') as check_mock:
            thread = start_maintained_db_update_thread(tmp_path, 60.0, app_state, stop_event=stop_event)
            assert thread is not None
            time.sleep(0.03)
            stop_event.set()
            thread.join(timeout=2)
            assert not thread.is_alive()
            assert check_mock.call_count == 0

    def test_ttl_sweep_thread_accepts_stop_event_and_handles_invalid_ttl_safely(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from web.backend.background import _resolve_sweep_ttl_seconds, start_ttl_sweep_thread

        results_dir = tmp_path / 'results'
        uploads_dir = tmp_path / 'uploads'
        results_dir.mkdir()
        uploads_dir.mkdir()
        fresh_file = results_dir / 'fresh.json'
        fresh_file.write_text('keep', encoding='utf-8')

        for bad_val in ('0', '-10', 'not-an-int'):
            caplog.clear()
            monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', bad_val)
            with caplog.at_level(logging.WARNING, logger='web.backend.background'):
                resolved = _resolve_sweep_ttl_seconds()
            assert resolved > 0
            assert any('RESPRO_WEB_RESULT_TTL' in rec.message for rec in caplog.records)

        # Valid TTL sweep deletes expired file and preserves fresh file, then exits via stop_event
        expired_file = uploads_dir / 'expired.txt'
        expired_file.write_text('old', encoding='utf-8')
        old_mtime = time.time() - 120
        import os

        os.utime(expired_file, (old_mtime, old_mtime))
        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '30')
        stop_event = threading.Event()
        thread = start_ttl_sweep_thread(results_dir, uploads_dir, stop_event=stop_event)
        try:
            deadline = time.monotonic() + 2.0
            while expired_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not expired_file.exists()
            assert fresh_file.is_file()
        finally:
            stop_event.set()
            thread.join(timeout=2)
        assert not thread.is_alive()

    def test_startup_validates_online_policy_before_bootstrap_and_fails_soft_with_local_db(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from web.backend.startup_config import load_startup_config

        data_dir = tmp_path / 'data'
        pd_dir = data_dir / 'project_databases'
        pd_dir.mkdir(parents=True)
        _make_project_db(pd_dir / 'local.db', name='local')

        monkeypatch.setenv('RESPRO_WEB_DATA_DIR', str(data_dir))
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'true')
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'online')
        monkeypatch.delenv('RESPRO_WEB_TRUSTED_PROXIES', raising=False)

        boot_mock = MagicMock()
        monkeypatch.setattr('web.backend.startup_config.bootstrap_missing_maintained_databases', boot_mock)
        with pytest.raises(RuntimeError, match='RESPRO_WEB_TRUSTED_PROXIES'):
            load_startup_config()
        boot_mock.assert_not_called()

        # With valid online policy and failing bootstrap, local DB keeps startup alive
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
        monkeypatch.setattr(
            'web.backend.startup_config.bootstrap_missing_maintained_databases',
            MagicMock(side_effect=RuntimeError('github unreachable')),
        )
        cfg = load_startup_config()
        assert len(cfg.project_db_uuid_index) == 1
