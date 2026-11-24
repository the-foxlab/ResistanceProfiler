"""User-facing CLI error rendering helpers bridging Click exceptions and Typer exits."""

from __future__ import annotations

from typing import NoReturn

import click
import typer


def cli_error(message: str, exit_code: int = 1) -> NoReturn:
    """Write ``Error: <message>`` to stderr and exit cleanly via ``typer.Exit``."""
    # Typer >= 0.26 vendors Click internally, so public click.ClickException is not
    # caught by Typer's runner; exiting via typer.Exit works across all Typer versions.
    click.echo(f'Error: {message}', err=True)
    raise typer.Exit(exit_code)


def render_click_exception(exc: click.ClickException) -> NoReturn:
    """Render a propagated ``click.ClickException`` and terminate via ``typer.Exit``."""
    exc.show()
    raise typer.Exit(exc.exit_code) from exc
