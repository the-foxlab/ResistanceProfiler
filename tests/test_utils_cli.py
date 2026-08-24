"""
Tests for CLI utilities - error handling and logging.

Covers: respro/utils/cli_errors.py, respro/utils/logging.py
- cli_error()
- render_click_exception()
- setup_logging()
- _LogHighlighter
"""

from __future__ import annotations

import logging

import click
import pytest
import typer

from respro.utils.cli_errors import cli_error, render_click_exception
from respro.utils.logging import _LogHighlighter, setup_logging


class TestCliError:
    """Tests for cli_error()."""

    def test_raises_typer_exit(self):
        """Should raise typer.Exit."""
        with pytest.raises(typer.Exit) as exc_info:
            cli_error('test error')
        assert exc_info.value.exit_code == 1

    def test_default_exit_code_is_1(self):
        """Should exit with code 1 by default."""
        with pytest.raises(typer.Exit) as exc_info:
            cli_error('error')
        assert exc_info.value.exit_code == 1

    def test_custom_exit_code(self):
        """Should support custom exit codes."""
        with pytest.raises(typer.Exit) as exc_info:
            cli_error('error', exit_code=2)
        assert exc_info.value.exit_code == 2

    def test_message_includes_error_prefix(self, capsys):
        """Should prepend 'Error: ' to message."""
        try:
            cli_error('something went wrong')
        except typer.Exit:
            pass
        captured = capsys.readouterr()
        assert 'Error: something went wrong' in captured.err


class TestRenderClickException:
    """Tests for render_click_exception()."""

    def test_raises_typer_exit(self):
        """Should raise typer.Exit."""
        exc = click.ClickException('test')
        with pytest.raises(typer.Exit):
            render_click_exception(exc)

    def test_preserves_exit_code(self):
        """Should preserve exception's exit code."""
        exc = click.ClickException('test')
        exc.exit_code = 3
        with pytest.raises(typer.Exit) as exc_info:
            render_click_exception(exc)
        assert exc_info.value.exit_code == 3

    def test_works_with_usage_error(self, capsys):
        """Should work with UsageError."""
        exc = click.UsageError('usage problem')
        try:
            render_click_exception(exc)
        except typer.Exit:
            pass
        captured = capsys.readouterr()
        assert 'usage problem' in captured.err.lower()


class TestSetupLogging:
    """Tests for setup_logging()."""

    def test_returns_logger(self):
        """Should return Logger instance."""
        logger = setup_logging()
        assert isinstance(logger, logging.Logger)
        assert logger.name == 'respro'

    def test_default_level_is_warning(self):
        """Should default to WARNING level."""
        logger = setup_logging()
        assert logger.level == logging.WARNING

    def test_verbosity_0_is_warning(self):
        """Should set WARNING for verbosity 0."""
        logger = setup_logging(verbosity=0)
        assert logger.level == logging.WARNING

    def test_verbosity_1_is_info(self):
        """Should set INFO for verbosity 1."""
        logger = setup_logging(verbosity=1)
        assert logger.level == logging.INFO

    def test_verbosity_2_is_debug(self):
        """Should set DEBUG for verbosity 2+."""
        logger = setup_logging(verbosity=2)
        assert logger.level == logging.DEBUG
        logger2 = setup_logging(verbosity=10)
        assert logger2.level == logging.DEBUG

    def test_handler_attached(self):
        """Should attach a handler."""
        logger = setup_logging()
        assert len(logger.handlers) > 0

    def test_rich_handler_used(self):
        """Should use RichHandler."""
        from rich.logging import RichHandler
        logger = setup_logging()
        assert any(isinstance(h, RichHandler) for h in logger.handlers)

    def test_idempotent(self):
        """Should be idempotent (not add multiple handlers)."""
        logger1 = setup_logging()
        handler_count = len(logger1.handlers)
        logger2 = setup_logging()
        assert len(logger2.handlers) == handler_count


class TestLogHighlighter:
    """Tests for _LogHighlighter."""

    def test_instantiates(self):
        """Should instantiate without error."""
        highlighter = _LogHighlighter()
        assert highlighter is not None

    def test_has_regex_patterns(self):
        """Should have regex patterns defined."""
        highlighter = _LogHighlighter()
        assert len(highlighter.highlights) > 0

    def test_patterns_match_strings(self):
        """Should have patterns that match expected strings."""
        highlighter = _LogHighlighter()
        # Just verify the patterns compile and exist
        assert any('quoted' in str(p) or 'str_val' in str(p) for p in highlighter.highlights)
        assert any('module' in str(p) or 'respro' in str(p) for p in highlighter.highlights)
