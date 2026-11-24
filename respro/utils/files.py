"""File existence and output-path resolution helpers."""

from __future__ import annotations

from pathlib import Path


def require_file(path: Path, label: str = 'File') -> Path:
    """Return ``Path(path)`` if it is an existing file, else raise ``FileNotFoundError``."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'{label} not found: {path}')
    return path


def resolve_output_file(path: Path, default_filename: str) -> Path:
    """Resolve an output path: append ``default_filename`` when ``path`` is a directory or has no suffix."""
    path = Path(path)
    if path.exists() and path.is_dir():
        return path / default_filename
    if path.suffix:
        return path
    return path / default_filename
