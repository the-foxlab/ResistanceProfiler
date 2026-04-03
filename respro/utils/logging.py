"""
Structured logging setup for ResistanceProfiler.
"""

import logging
import sys


# ANSI escape codes — only applied when writing to a real terminal
_RESET = '\033[0m'
_COLOURS = {
    logging.DEBUG:    '\033[37m',    # white / light grey
    logging.INFO:     '\033[32m',    # green
    logging.WARNING:  '\033[33m',    # orange / yellow
    logging.ERROR:    '\033[31m',    # red
    logging.CRITICAL: '\033[31;1m',  # bold red
}


class _ColourFormatter(logging.Formatter):
    """
    Formatter that wraps each line in ANSI colour codes when connected to a TTY.
    """

    def __init__(self, fmt: str, datefmt: str, stream) -> None:
        super().__init__(fmt, datefmt=datefmt)
        self._use_colour = hasattr(stream, 'isatty') and stream.isatty()

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if self._use_colour:
            colour = _COLOURS.get(record.levelno, '')
            return f'{colour}{msg}{_RESET}'
        return msg


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
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        fmt = _ColourFormatter(
            '[%(asctime)s] %(levelname)-8s %(name)s — %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            stream=sys.stderr,
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    return logger
