"""File validation helpers."""

from __future__ import annotations

from pathlib import Path


def require_file(path: Path, label: str = 'File') -> Path:
    """
    Raise if *path* does not point to an existing file.

    :param path: path to check
    :param label: human-readable name used in the error message
    :return: the resolved path
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'{label} not found: {path}')
    return path


def resolve_output_file(path: Path, default_filename: str) -> Path:
    """
    Resolve an output option that may point to a directory or an explicit filename.

    Rules:
    - existing directory -> ``path / default_filename``
    - path with suffix (e.g. ``report.html``) -> treat as explicit file path
    - path without suffix (e.g. ``output``) -> treat as directory and append default filename

    :param path: user-provided output path
    :param default_filename: filename used when ``path`` is interpreted as a directory
    :return: resolved output file path
    """
    path = Path(path)
    if path.exists() and path.is_dir():
        return path / default_filename
    if path.suffix:
        return path
    return path / default_filename

