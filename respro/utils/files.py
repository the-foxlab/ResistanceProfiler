"""File validation helpers."""

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

