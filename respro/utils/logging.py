"""Structured Rich logging setup for the ``respro`` package."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.highlighter import RegexHighlighter
from rich.logging import RichHandler
from rich.theme import Theme


class _LogHighlighter(RegexHighlighter):
    """Highlight quoted strings and ``respro.*`` module paths in log messages."""

    highlights = [
        r'(?P<str_val>\'[^\']*\'|\"[^\"]*\")',
        r'(?P<module>respro(?:\.[a-z][a-z0-9_]*)*)',
    ]


_LOG_THEME = Theme({'str_val': 'green1', 'module': 'bold cyan'})

# Shared stderr console lets Rich pause CLI status spinners cleanly when log lines emit.
err_console = Console(stderr=True, theme=_LOG_THEME)


def setup_logging(verbosity: int = 0) -> logging.Logger:
    """Configure and return the ``respro`` package logger (0=WARNING, 1=INFO, 2+=DEBUG)."""
    level = {0: logging.WARNING, 1: logging.INFO}.get(max(0, verbosity), logging.DEBUG)

    logger = logging.getLogger('respro')
    logger.setLevel(level)

    if not logger.handlers:
        handler = RichHandler(
            level=level,
            show_path=False,
            rich_tracebacks=False,
            log_time_format='[%Y-%m-%d %H:%M:%S]',
            highlighter=_LogHighlighter(),
            console=err_console,
        )
        handler.setFormatter(logging.Formatter('%(name)s — %(message)s'))
        logger.addHandler(handler)
    else:
        for existing in logger.handlers:
            existing.setLevel(level)

    return logger
