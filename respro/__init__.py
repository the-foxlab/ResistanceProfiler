"""ResistanceProfiler — pathogen-agnostic antiviral resistance profiling."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version('respro')
except PackageNotFoundError:  # pragma: no cover - source tree without installed metadata
    __version__ = '0.0.0'

