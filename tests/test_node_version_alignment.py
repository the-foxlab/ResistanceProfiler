"""Regression guard for the frontend Node.js version alignment invariant.

Auditing found that the declared Node major drifted across four surfaces:
``web/frontend/package.json`` ``engines.node``, the CI ``frontend-tests`` job,
``Dockerfile.web``'s base image, and ``web/README.md``. These tests pin a
single supported Node major across all four so the drift cannot silently
return, and require ``engine-strict=true`` so CI fails fast on future drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_JSON = REPO_ROOT / 'web' / 'frontend' / 'package.json'
_NPMRC = REPO_ROOT / 'web' / 'frontend' / '.npmrc'
_WORKFLOW = REPO_ROOT / '.github' / 'workflows' / 'frontend-tests.yml'
_DOCKERFILE = REPO_ROOT / 'Dockerfile.web'
_README = REPO_ROOT / 'web' / 'README.md'


def _package_json_node_major() -> int:
    """Major version declared in ``package.json`` ``engines.node`` (e.g. '>=24.0.0' -> 24)."""
    data = json.loads(_PACKAGE_JSON.read_text(encoding='utf-8'))
    spec = data['engines']['node']
    match = re.search(r'(\d+)', spec)
    assert match, f'No major version found in engines.node spec: {spec!r}'
    return int(match.group(1))


def _ci_node_major() -> int:
    """Major version pinned in the CI ``frontend-tests`` job's ``setup-node`` step."""
    text = _WORKFLOW.read_text(encoding='utf-8')
    # The only setup-node invocation in this workflow is the frontend-tests job.
    match = re.search(r"node-version:\s*['\"]?(\d+)", text)
    assert match, 'No node-version found in CI workflow'
    return int(match.group(1))


def _dockerfile_node_major() -> int:
    """Major version of the ``node:<N>-alpine`` base image used to build the frontend."""
    text = _DOCKERFILE.read_text(encoding='utf-8')
    match = re.search(r'^FROM\s+node:(\d+)-alpine', text, re.MULTILINE)
    assert match, 'No node:<N>-alpine base image found in Dockerfile.web'
    return int(match.group(1))


def _readme_node_major() -> int:
    """Major version stated in ``web/README.md`` (e.g. 'Node.js 24+')."""
    text = _README.read_text(encoding='utf-8')
    match = re.search(r'Node\.js\s+(\d+)\+', text)
    assert match, 'No "Node.js <N>+" statement found in web/README.md'
    return int(match.group(1))


class TestNodeVersionAlignment:
    """All four surfaces must declare exactly one Node major version."""

    def test_package_json_declares_supported_major(self) -> None:
        major = _package_json_node_major()
        # Supported majors: 22 (proven fallback) or 24 (spike-confirmed target).
        assert major in (22, 24), f'package.json engines.node major {major} not in supported set'

    def test_ci_uses_same_major_as_package_json(self) -> None:
        assert _ci_node_major() == _package_json_node_major()

    def test_dockerfile_uses_same_major_as_package_json(self) -> None:
        assert _dockerfile_node_major() == _package_json_node_major()

    def test_readme_states_same_major_as_package_json(self) -> None:
        assert _readme_node_major() == _package_json_node_major()

    def test_npmrc_enforces_engine_strict(self) -> None:
        """``engine-strict=true`` makes npm fail fast if the runner drifts from engines.node."""
        assert _NPMRC.is_file(), 'web/frontend/.npmrc missing'
        text = _NPMRC.read_text(encoding='utf-8')
        assert 'engine-strict=true' in text, f'.npmrc missing engine-strict=true: {text!r}'
