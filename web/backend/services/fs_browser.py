"""Filesystem browsing helpers for the web API."""

from __future__ import annotations

from pathlib import Path


def list_directory(path: Path | None, *, allowed_roots: tuple[Path, ...]) -> dict[str, object]:
    """List files and directories for a given directory path.

    :param path: directory path to browse; root is used when omitted
    :param allowed_roots: allowed filesystem roots for browsing
    :return: dictionary with normalized path and child items
    """
    browse_path = path if path is not None else allowed_roots[0]
    resolved_path = browse_path.expanduser().resolve()
    if not _is_within_allowed_roots(resolved_path, allowed_roots):
        raise ValueError(f'Path is outside allowed roots: {resolved_path}')
    if not resolved_path.exists():
        raise FileNotFoundError(f'Path does not exist: {resolved_path}')
    if not resolved_path.is_dir():
        raise ValueError(f'Path is not a directory: {resolved_path}')

    entries: list[dict[str, str]] = []
    for child in sorted(resolved_path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        item_type = 'dir' if child.is_dir() else 'file'
        entries.append({'name': child.name, 'path': str(child), 'type': item_type})

    parent_path = None
    if resolved_path.parent != resolved_path:
        parent_path = str(resolved_path.parent)

    return {
        'path': str(resolved_path),
        'parent_path': parent_path,
        'items': entries,
    }


def _is_within_allowed_roots(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    """Return whether path is contained in any allowed root."""
    for root in allowed_roots:
        if path == root or root in path.parents:
            return True
    return False
