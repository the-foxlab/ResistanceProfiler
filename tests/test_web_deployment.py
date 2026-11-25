"""Regression tests for M2C web deployment, container, compose, and docs hardening."""

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
COMPOSE_WEB_2 = REPO_ROOT / 'docker-compose.web_2.yml'
PROD_COMPOSE = REPO_ROOT / 'production' / 'docker-compose.web.yml'
PROD_ENV_EXAMPLE = REPO_ROOT / 'production' / '.env.example'
PROD_DEPLOYMENT_MD = REPO_ROOT / 'production' / 'deployment.md'
WEB_README = REPO_ROOT / 'web' / 'README.md'
WEBAPP_DOCS = REPO_ROOT / 'docs' / 'docs' / 'webapp.md'

_PINNED_TAG_RE = re.compile(
    r'^(?:node:\d+-alpine\d+\.\d+|python:\d+\.\d+\.\d+-slim-[a-z0-9]+|redis:\d+\.\d+\.\d+-alpine(?:\d+\.\d+)?)$'
)
_DIGEST_PIN_RE = re.compile(r'@sha256:[0-9a-f]{64}$')


def _read_required(path: Path) -> str:
    assert path.is_file(), f'Expected tracked deployment file to exist: {path}'
    return path.read_text(encoding='utf-8')


def _read_optional(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f'Optional/gitignored file not present: {path.relative_to(REPO_ROOT)}')
    return path.read_text(encoding='utf-8')


def _load_yaml(path: Path) -> dict[str, Any]:
    yaml = pytest.importorskip('yaml')
    text = _read_optional(path)
    data = yaml.safe_load(text)
    assert isinstance(data, dict), f'Expected mapping in {path}'
    return data


def _extract_compose_service_block(compose_text: str, service_name: str) -> str:
    """Extract an individual service block under ``services:`` using indentation."""
    lines = compose_text.splitlines()
    in_services = False
    service_indent: int | None = None
    captured: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not in_services:
            if re.match(r'^services:\s*(?:#.*)?$', line):
                in_services = True
            continue

        if stripped and not stripped.startswith('#'):
            indent = len(line) - len(line.lstrip(' '))
            if indent == 0:
                if captured:
                    break
                in_services = False
                continue

            if service_indent is None:
                if re.match(rf'^\s+{re.escape(service_name)}:\s*(?:#.*)?$', line):
                    service_indent = indent
                    captured.append(line)
                continue

            if indent <= service_indent:
                break

        if service_indent is not None:
            captured.append(line)

    assert captured, f'Service {service_name!r} not found under services:'
    return '\n'.join(captured)


def _is_pinned_image_ref(image_ref: str) -> bool:
    cleaned = image_ref.strip().strip('"\'')
    if cleaned.startswith('${') and ':-' in cleaned and cleaned.endswith('}'):
        cleaned = cleaned.split(':-', 1)[1][:-1]
    if cleaned.endswith(':latest') or ':latest@' in cleaned:
        return False
    if _DIGEST_PIN_RE.search(cleaned):
        return True
    return bool(_PINNED_TAG_RE.match(cleaned))


class TestComposeFiles:
    """Verify Docker Compose syntax, hardening caps, build parity, and proxy docs."""

    @pytest.mark.parametrize(
        'rel_path',
        [
            'docker-compose.web.yml',
            'docker-compose.web_2.yml',
            'production/docker-compose.web.yml',
        ],
    )
    def test_compose_yaml_valid(self, rel_path: str) -> None:
        data = _load_yaml(REPO_ROOT / rel_path)
        services = data.get('services')
        assert isinstance(services, dict), f'{rel_path} must define a services mapping'
        for name in ('redis', 'respro-web', 'respro-worker'):
            assert name in services, f'{rel_path} missing service {name!r}'

    @pytest.mark.parametrize(
        'rel_path',
        [
            'docker-compose.web_2.yml',
            'production/docker-compose.web.yml',
        ],
    )
    def test_tmpfs_tmp_mounts_have_size_cap(self, rel_path: str) -> None:
        text = _read_optional(REPO_ROOT / rel_path)
        tmp_entries = re.findall(r'-\s+/tmp:[^\s#]+', text)
        assert tmp_entries, f'Expected /tmp tmpfs entries in {rel_path}'
        assert all('size=' in entry for entry in tmp_entries), (
            f'Every /tmp tmpfs mount in {rel_path} must declare size=: {tmp_entries}'
        )

        yaml = pytest.importorskip('yaml')
        parsed = yaml.safe_load(text)
        services = parsed['services']
        for svc_name in ('redis', 'respro-web', 'respro-worker'):
            tmpfs_list = services[svc_name].get('tmpfs') or []
            tmp_mounts = [item for item in tmpfs_list if str(item).startswith('/tmp:')]
            assert tmp_mounts, f'{rel_path} service {svc_name} missing /tmp tmpfs mount'
            assert all('size=' in str(item) for item in tmp_mounts), (
                f'{rel_path} service {svc_name} /tmp mount missing size=: {tmp_mounts}'
            )

        web_tmp = [str(item) for item in services['respro-web']['tmpfs'] if str(item).startswith('/tmp:')]
        size_match = re.search(r'size=(\d+)([mg])', web_tmp[0])
        assert size_match, f'Expected explicit size unit on respro-web /tmp tmpfs: {web_tmp}'
        size_mb = int(size_match.group(1)) * (1024 if size_match.group(2) == 'g' else 1)
        assert size_mb >= 1024, (
            f'{rel_path} respro-web /tmp tmpfs ({size_mb}m) must accommodate 1 GiB max_bam_size uploads'
        )

    @pytest.mark.parametrize(
        'rel_path',
        [
            'docker-compose.web.yml',
            'docker-compose.web_2.yml',
        ],
    )
    def test_worker_defines_build_block(self, rel_path: str) -> None:
        text = _read_optional(REPO_ROOT / rel_path)
        worker_block = _extract_compose_service_block(text, 'respro-worker')
        assert re.search(r'^\s+build:\s*$', worker_block, re.MULTILINE), (
            f'{rel_path} respro-worker must define a build: block'
        )
        assert 'Dockerfile.web' in worker_block, (
            f'{rel_path} respro-worker build: block must reference Dockerfile.web'
        )

        yaml = pytest.importorskip('yaml')
        parsed = yaml.safe_load(text)
        services = parsed['services']
        assert services['respro-worker']['build'] == services['respro-web']['build'], (
            f'{rel_path} respro-worker build configuration must match respro-web'
        )

    def test_imprint_volume_comment_under_respro_web(self) -> None:
        text = _read_required(COMPOSE_WEB)
        web_block = _extract_compose_service_block(text, 'respro-web')
        worker_block = _extract_compose_service_block(text, 'respro-worker')
        assert ':/data/imprint.html' in web_block, (
            'docker-compose.web.yml must place the imprint.html mount comment under respro-web'
        )
        assert 'imprint.html' not in worker_block, (
            'docker-compose.web.yml must not reference imprint.html under respro-worker'
        )

    @pytest.mark.parametrize(
        'rel_path',
        [
            'docker-compose.web.yml',
            'docker-compose.web_2.yml',
            'production/docker-compose.web.yml',
        ],
    )
    def test_compose_redis_images_are_pinned(self, rel_path: str) -> None:
        text = _read_optional(REPO_ROOT / rel_path)
        redis_block = _extract_compose_service_block(text, 'redis')
        match = re.search(r'^\s+image:\s*(\S+)', redis_block, re.MULTILINE)
        assert match, f'Expected image: in {rel_path} redis service'
        image_ref = match.group(1)
        assert _is_pinned_image_ref(image_ref), (
            f'{rel_path} redis image must be pinned by patch/digest, got {image_ref!r}'
        )

    @pytest.mark.parametrize(
        'rel_path',
        [
            'docker-compose.web.yml',
            'docker-compose.web_2.yml',
            'production/docker-compose.web.yml',
        ],
    )
    def test_compose_trusted_proxies_mentions_docker_bridge(self, rel_path: str) -> None:
        text = _read_optional(REPO_ROOT / rel_path)
        assert 'RESPRO_WEB_TRUSTED_PROXIES' in text, (
            f'{rel_path} should document RESPRO_WEB_TRUSTED_PROXIES'
        )
        assert 'bridge gateway' in text.lower() or '172.16.0.0/12' in text, (
            f'{rel_path} must document Docker bridge gateway/subnet for RESPRO_WEB_TRUSTED_PROXIES'
        )

    def test_compose_web_2_has_no_stale_with_access_or_mode_contradiction(self) -> None:
        text = _read_optional(COMPOSE_WEB_2)
        assert 'with_access' not in text and 'with _access' not in text, (
            'docker-compose.web_2.yml still references stale production/with_access path'
        )
        assert 'RESPRO_WEB_DEPLOYMENT_MODE=local' in text
        header = '\n'.join(text.splitlines()[:30])
        assert re.search(r'^\s*#\s*[•*-]\s*local\s+mode\b', header, re.MULTILINE | re.IGNORECASE), (
            'docker-compose.web_2.yml header should state local mode to match RESPRO_WEB_DEPLOYMENT_MODE=local'
        )
        assert not re.search(
            r'^\s*#\s*[•*-]\s*online\s+mode\b', header, re.MULTILINE | re.IGNORECASE
        ), 'docker-compose.web_2.yml header contradicts RESPRO_WEB_DEPLOYMENT_MODE=local'


class TestDockerfileWeb:
    """Verify Dockerfile.web root-owned /app, cache dirs, dead env removal, and base pins."""

    def test_dockerfile_keeps_app_owned_by_root(self) -> None:
        text = _read_required(DOCKERFILE_WEB)
        assert 'chown -R respro:respro /app' not in text
        assert not re.search(r'\bchown\b[^\n]*\s/app(?:\s|$|/)', text), (
            'Dockerfile.web must keep /app owned by root:root'
        )
        assert re.search(r'\bchmod\b[^\n]*\s/app(?:\s|$)', text), (
            'Dockerfile.web should explicitly set read/execute permissions on /app'
        )
        assert 'chown respro:respro /data' in text

    def test_dockerfile_removes_dead_frontend_dist_env(self) -> None:
        text = _read_required(DOCKERFILE_WEB)
        assert 'RESPRO_FRONTEND_DIST' not in text

    def test_dockerfile_sets_writable_cache_env_vars(self) -> None:
        text = _read_required(DOCKERFILE_WEB)
        assert 'ENV MPLCONFIGDIR=/tmp/' in text
        assert 'ENV XDG_CACHE_HOME=/tmp/' in text

    def test_dockerfile_base_images_are_pinned(self) -> None:
        text = _read_required(DOCKERFILE_WEB)
        from_images = re.findall(r'^FROM\s+(\S+)', text, re.MULTILINE)
        assert len(from_images) == 3, f'Expected 3 FROM stages in Dockerfile.web, got {from_images}'
        for image_ref in from_images:
            assert _is_pinned_image_ref(image_ref), (
                f'Dockerfile.web base image must be reproducibly pinned: {image_ref!r}'
            )


class TestEntrypointWebScript:
    """Verify docker/entrypoint.web.sh syntax, symlink safety, and startup locking."""

    @pytest.mark.parametrize('shell', ['sh', 'bash'])
    def test_entrypoint_shell_syntax_valid(self, shell: str) -> None:
        shell_bin = shutil.which(shell)
        if shell_bin is None:
            pytest.skip(f'{shell} executable not found on PATH')
        assert ENTRYPOINT_WEB.is_file()
        result = subprocess.run(
            [shell_bin, '-n', str(ENTRYPOINT_WEB)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f'{shell} -n failed: {result.stderr}'

    def test_entrypoint_chown_does_not_dereference_symlinks(self) -> None:
        text = _read_required(ENTRYPOINT_WEB)
        chown_lines = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith('#') and re.search(r'\bchown\b', ln)
        ]
        assert chown_lines, 'Expected at least one chown invocation in entrypoint.web.sh'
        for ln in chown_lines:
            assert re.search(
                r'\bchown\b[^#\n]*(?:\s-(?:[A-Za-z]*h[A-Za-z]*)|\s--no-dereference)(?:\s|$)',
                ln,
            ), f'chown in entrypoint.web.sh must not dereference symlinks: {ln!r}'
        assert 'find -P /data' in text, (
            'entrypoint.web.sh should traverse /data physically with find -P'
        )

    def test_entrypoint_handles_concurrent_startup_safely(self) -> None:
        text = _read_required(ENTRYPOINT_WEB)
        assert 'set -e' in text
        assert 'flock' in text, 'entrypoint.web.sh must serialize concurrent startups with flock'
        assert '! -L' in text, 'entrypoint.web.sh must guard lockfile/data against symlinks'
        assert 'exec gosu respro "$@"' in text


class TestProductionBundle:
    """Verify production/ deployment guide, compose, and .env.example alignment."""

    def test_production_deployment_doc_has_no_with_access(self) -> None:
        text = _read_optional(PROD_DEPLOYMENT_MD)
        assert 'with _access' not in text and 'with_access' not in text

    def test_production_preserves_redis_password_naming(self) -> None:
        prod_compose = _read_optional(PROD_COMPOSE)
        prod_env = _read_optional(PROD_ENV_EXAMPLE)
        prod_md = _read_optional(PROD_DEPLOYMENT_MD)
        for name, text in (
            ('production/docker-compose.web.yml', prod_compose),
            ('production/.env.example', prod_env),
            ('production/deployment.md', prod_md),
        ):
            assert 'REDIS_PASSWORD' in text, f'{name} must preserve REDIS_PASSWORD'
            assert 'RESPRO_REDIS_PASSWORD' not in text, (
                f'{name} must not mix in RESPRO_REDIS_PASSWORD'
            )

    def test_production_images_and_trusted_proxies_hardened(self) -> None:
        prod_env = _read_optional(PROD_ENV_EXAMPLE)
        prod_md = _read_optional(PROD_DEPLOYMENT_MD)

        redis_match = re.search(r'^REDIS_IMAGE=(\S+)', prod_env, re.MULTILINE)
        assert redis_match and _is_pinned_image_ref(redis_match.group(1)), (
            f'production/.env.example REDIS_IMAGE must be pinned: {redis_match}'
        )
        respro_match = re.search(r'^RESPRO_IMAGE=(\S+)', prod_env, re.MULTILINE)
        assert respro_match and not respro_match.group(1).endswith(':latest'), (
            'production/.env.example RESPRO_IMAGE should recommend pinned :sha-/<commit> instead of :latest'
        )

        proxies_match = re.search(r'^RESPRO_WEB_TRUSTED_PROXIES=(\S+)', prod_env, re.MULTILINE)
        assert proxies_match, 'production/.env.example missing RESPRO_WEB_TRUSTED_PROXIES'
        assert '172.16.0.0/12' in proxies_match.group(1)
        assert 'canonical' in prod_env.lower() or 'no host bits' in prod_env.lower()

        assert '172.16.0.0/12' in prod_md
        assert 'docker inspect' in prod_md


class TestWebDocumentation:
    """Verify web/README.md and docs/docs/webapp.md match backend and frontend reality."""

    def test_webapp_docs_remove_stale_api_token(self) -> None:
        text = _read_required(WEBAPP_DOCS)
        assert 'RESPRO_WEB_API_TOKEN' not in text

    def test_webapp_docs_cover_trusted_proxies_ttl_queue_and_dev_proxy(self) -> None:
        text = _read_required(WEBAPP_DOCS)
        assert 'RESPRO_WEB_TRUSTED_PROXIES' in text
        assert 'bridge gateway' in text.lower()
        assert '172.16.0.0/12' in text
        assert 'RESPRO_WEB_RESULT_TTL' in text
        assert 'RESPRO_WEB_SESSION_TTL' in text
        assert 'python -m web.backend.worker' in text
        assert 'redis' in text.lower()
        assert 'pip install -e .' in text
        assert '/api' in text and 'vite' in text.lower() and 'proxy' in text.lower()
        assert 'separate-origin' in text.lower()
        assert 'unsupported' in text.lower() or 'not supported' in text.lower()

    def test_web_readme_documents_local_setup_worker_redis_and_proxy(self) -> None:
        text = _read_required(WEB_README)
        assert 'pip install -e .' in text
        assert 'pip install -r web/backend/requirements.txt' in text
        assert 'python -m web.backend.main' in text
        assert 'python -m web.backend.worker' in text
        assert 'redis' in text.lower()
        assert 'RESPRO_WEB_RESULT_TTL' in text
        assert 'RESPRO_WEB_TRUSTED_PROXIES' in text
        assert '/api' in text and 'vite' in text.lower() and 'proxy' in text.lower()
        assert 'separate-origin' in text.lower()
        assert 'unsupported' in text.lower() or 'not supported' in text.lower()
