"""Utility helpers: validation."""

from pathlib import Path


def require_file(path: Path, label: str = 'File') -> Path:
    """Raise if *path* does not point to an existing file."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'{label} not found: {path}')
    return path


def require_dir(path: Path, create: bool = False, label: str = 'Directory') -> Path:
    """Raise if *path* does not point to a directory. Optionally create it."""
    path = Path(path)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise NotADirectoryError(f'{label} not found: {path}')
    return path


def validate_strand(strand: str) -> str:
    """Normalise strand to '+' or '-'."""
    if strand in ('+', '1', 'plus', 'forward'):
        return '+'
    if strand in ('-', '-1', 'minus', 'reverse'):
        return '-'
    raise ValueError(f'Invalid strand value: {strand!r}')


def validate_af(af: float) -> float:
    """Clamp and validate an allele frequency value."""
    if not 0.0 <= af <= 1.0:
        raise ValueError(f'Allele frequency out of range [0, 1]: {af}')
    return af

