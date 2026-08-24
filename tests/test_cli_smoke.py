"""
Smoke tests for the top-level ``respro`` CLI.
"""

from __future__ import annotations

from typer.testing import CliRunner

from respro import __version__
from respro.cli.main import app


class TestCliSmoke:
    """Minimal end-to-end checks that the CLI group builds and runs."""

    def test_version_flag_prints_version_and_exits_zero(self):
        """``respro --version`` should print the package version and exit 0."""
        result = CliRunner().invoke(app, ['--version'])
        assert result.exit_code == 0, result.output
        assert __version__ in result.output

    def test_help_flag_exits_zero_and_lists_commands(self):
        """``respro --help`` should exit 0 and mention the application name."""
        result = CliRunner().invoke(app, ['--help'])
        assert result.exit_code == 0, result.output
        # The app help string contains "ResistanceProfiler".
        assert 'respro' in result.output.lower() or 'resistance' in result.output.lower()

    def test_no_args_shows_help_and_exits_nonzero(self):
        """``respro`` with no args should show help (no_args_is_help=True)."""
        result = CliRunner().invoke(app, [])
        # no_args_is_help prints help and exits with code 2 (Click convention).
        assert result.exit_code == 2, result.output
        assert 'respro' in result.output.lower() or 'resistance' in result.output.lower()
