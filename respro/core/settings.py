"""Core runtime configuration defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True)
class CoreAlignmentConfig:
    """Alignment threshold defaults used by core matching routines."""

    min_identity: float
    min_coverage: float


@dataclass(frozen=True)
class CoreConfig:
    """Bundled core configuration loaded from respro/config/defaults.toml."""

    alignment: CoreAlignmentConfig


def _load_core_config() -> CoreConfig:
    defaults_path = files('respro.config').joinpath('defaults.toml')
    payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))
    alignment = payload['alignment']

    return CoreConfig(
        alignment=CoreAlignmentConfig(
            min_identity=float(alignment['min_identity']),
            min_coverage=float(alignment['min_coverage']),
        )
    )


CORE_CONFIG = _load_core_config()
