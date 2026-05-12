"""Core runtime configuration defaults."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoreConfig:
    """Placeholder core configuration."""


def _load_core_config() -> CoreConfig:
    return CoreConfig()


CORE_CONFIG = _load_core_config()
