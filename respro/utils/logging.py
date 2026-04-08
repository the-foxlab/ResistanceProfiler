"""
Structured logging setup for ResistanceProfiler.
"""

import logging

from rich.console import Console
from rich.highlighter import RegexHighlighter
from rich.logging import RichHandler
from rich.theme import Theme


class _LogHighlighter(RegexHighlighter):
    """Highlights quoted strings and respro.* module paths in log messages."""

    highlights = [
        r'(?P<str_val>\'[^\']*\'|\"[^\"]*\")',   # single- or double-quoted strings
        r'(?P<module>respro(?:\.[a-z][a-z0-9_]*)*)',  # respro or respro.module.path identifiers
    ]


_LOG_THEME = Theme({
    'str_val': 'green1',    # same shade as Rich's repr.str
    'module': 'bold cyan',
})

# Shared stderr console used by both RichHandler and CLI status spinners.
# Sharing the same Console instance lets Rich coordinate spinner animations
# with log output — the spinner pauses cleanly when a log line is emitted.
err_console = Console(stderr=True, theme=_LOG_THEME)


def setup_logging(verbosity: int = 0) -> logging.Logger:
    """
    Configure and return the package-level logger.

    :param verbosity: 0 = WARNING, 1 = INFO, 2+ = DEBUG
    :return: configured Logger instance
    """
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)

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

    return logger
