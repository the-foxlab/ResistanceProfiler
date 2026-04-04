"""Utility helpers: validation."""

from pathlib import Path


def require_file(path: Path, label: str = 'File') -> Path:
    """Raise if *path* does not point to an existing file."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'{label} not found: {path}')
    return path


def validate_strand(strand: str) -> str:
    """Normalise strand to '+' or '-'."""
    if strand in ('+', '1', 'plus', 'forward'):
        return '+'
    if strand in ('-', '-1', 'minus', 'reverse'):
        return '-'
    raise ValueError(f'Invalid strand value: {strand!r}')

