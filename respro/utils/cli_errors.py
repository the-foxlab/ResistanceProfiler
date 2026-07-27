"""
User-facing CLI error rendering helpers.

Typer >= 0.26 vendors Click internally as ``typer._click`` and its command
``_main`` only catches the *vendored* ``typer._click.exceptions.ClickException``.
A public ``click.ClickException`` raised inside a command callback is therefore no
longer caught/rendered by Typer and surfaces as a raw traceback.

Command callbacks render user-facing errors explicitly via :func:`cli_error`, which
writes the standard ``Error: <message>`` line to stderr (matching Click's own
``ClickException.show()`` formatting) and exits through ``typer.Exit`` — which
Typer handles cleanly in every version. Lower-level helpers in
``profile_helpers.py`` and ``sync.py`` keep raising ``click.ClickException`` because
they are unit-tested directly with ``pytest.raises(click.ClickException)``; command
callbacks catch those propagated exceptions and route them through
:func:`render_click_exception` at the command boundary.
"""

from __future__ import annotations

import click
import typer


def cli_error(message: str, exit_code: int = 1) -> None:
    """Render a user-facing CLI error message and exit.

    Writes ``Error: <message>`` to stderr (Click's standard error formatting) and
    raises :class:`typer.Exit` so Typer terminates the process cleanly without a
    traceback. Use this from command callbacks for validation failures and for
    re-raising helper-propagated :class:`click.ClickException` instances.

    :param message: human-readable error message
    :param exit_code: process exit code (default 1)
    """
    click.echo(f'Error: {message}', err=True)
    raise typer.Exit(exit_code)


def render_click_exception(exc: click.ClickException) -> None:
    """Render a propagated :class:`click.ClickException` and exit.

    Thin adapter for command-boundary ``except click.ClickException`` clauses: it
    preserves Click's own :meth:`ClickException.show` rendering (so ``UsageError``
    still prints usage context) and exits with the exception's code.

    :param exc: the propagated Click exception
    """
    exc.show()
    raise typer.Exit(exc.exit_code) from exc
