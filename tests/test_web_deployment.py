"""Regression tests for tracked web Docker, Compose, entrypoint, and docs hardening."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE_WEB = REPO_ROOT / 'Dockerfile.web'
ENTRYPOINT_WEB = REPO_ROOT / 'docker' / 'entrypoint.web.sh'
COMPOSE_WEB = REPO_ROOT / 'docker-compose.web.yml'
WEBAPP_DOCS = REPO_ROOT / 'docs' / 'docs' / 'webapp.md'

_PINNED_TAG_RE = re.compile(
    r'^(?:node:\d+-alpine\d+\.\d+|python:\d+\.\d+\.\d+-slim-[a-z0-9]+|redis:\d+\.\d+\.\d+-alpine(?:\d+\.\d+)?)$'
)
_DIGEST_PIN_RE = re.compile(r'@sha256:[0-9a-f]{64}$')


def _is_pinned_image_ref(image_ref: str) -> bool:
    cleaned = image_ref.strip().strip('"\'')
    if cleaned.startswith('${') and ':-' in cleaned and cleaned.endswith('}'):
        cleaned = cleaned.split(':-', 1)[1][:-1]
    if cleaned.endswith(':latest') or ':latest@' in cleaned:
        return False
    return bool(_DIGEST_PIN_RE.search(cleaned) or _PINNED_TAG_RE.match(cleaned))


class TestComposeFile:
    """Verify tracked docker-compose.web.yml structure, build parity, and image pinning."""

    def test_compose_services_build_parity_and_pinned_redis(self) -> None:
        yaml = pytest.importorskip('yaml')
        parsed: dict[str, Any] = yaml.safe_load(COMPOSE_WEB.read_text(encoding='utf-8'))
        services = parsed['services']
        for name in ('redis', 'respro-web', 'respro-worker'):
            assert name in services

        assert services['respro-worker']['build'] == services['respro-web']['build']
        assert _is_pinned_image_ref(str(services['redis']['image']))


class TestDockerfileWeb:
    """Verify Dockerfile.web permissions, cache dirs, dead env removal, and base pins."""

    def test_dockerfile_hardening_and_pinned_stages(self) -> None:
        text = DOCKERFILE_WEB.read_text(encoding='utf-8')
        assert 'chown -R respro:respro /app' not in text
        assert not re.search(r'\bchown\b[^\n]*\s/app(?:\s|$|/)', text)
        assert 'chown respro:respro /data' in text
        assert 'RESPRO_FRONTEND_DIST' not in text
        assert 'ENV MPLCONFIGDIR=/tmp/' in text
        assert 'ENV XDG_CACHE_HOME=/tmp/' in text

        from_images = re.findall(r'^FROM\s+(\S+)', text, re.MULTILINE)
        assert len(from_images) == 3
        for image_ref in from_images:
            assert _is_pinned_image_ref(image_ref), f'Unpinned FROM image: {image_ref!r}'


class TestEntrypointWebScript:
    """Verify docker/entrypoint.web.sh syntax, symlink safety, and startup locking."""

    @pytest.mark.parametrize('shell', ['sh', 'bash'])
    def test_entrypoint_shell_syntax_valid(self, shell: str) -> None:
        shell_bin = shutil.which(shell)
        if shell_bin is None:
            pytest.skip(f'{shell} executable not found on PATH')
        result = subprocess.run([shell_bin, '-n', str(ENTRYPOINT_WEB)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, f'{shell} -n failed: {result.stderr}'

    def test_entrypoint_symlink_safe_and_serialized(self) -> None:
        text = ENTRYPOINT_WEB.read_text(encoding='utf-8')
        chown_lines = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith('#') and re.search(r'\bchown\b', ln)
        ]
        assert chown_lines
        for ln in chown_lines:
            assert re.search(
                r'\bchown\b[^#\n]*(?:\s-(?:[A-Za-z]*h[A-Za-z]*)|\s--no-dereference)(?:\s|$)',
                ln,
            ), f'chown must not dereference symlinks: {ln!r}'
        assert 'find -P /data' in text
        assert 'flock' in text
        assert 'exec gosu respro "$@"' in text


class TestWebDocumentation:
    """Verify docs do not advertise removed authentication env vars."""

    def test_webapp_docs_remove_stale_api_token(self) -> None:
        assert 'RESPRO_WEB_API_TOKEN' not in WEBAPP_DOCS.read_text(encoding='utf-8')
