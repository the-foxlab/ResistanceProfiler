"""CLI entry point for ResistanceProfiler."""

from __future__ import annotations

from typing import Annotated

import typer

from respro import __version__
from respro.cli import classify, fasta, init, maintained_db, manage, regenerate, vcf
from respro.utils.logging import setup_logging

app = typer.Typer(
    help='ResistanceProfiler — agnostic antiviral resistance profiling framework.',
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    context_settings={'help_option_names': ['-h', '--help']},
)

for _module in (maintained_db, init, vcf, fasta, manage, regenerate, classify):
    _module.register(app)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f'respro {__version__}')
        raise typer.Exit()


@app.callback()
def _callback(
    verbose: Annotated[
        int, typer.Option(
            '--verbose', '-v', count=True, metavar='', show_default=False,
            help='Increase verbosity (-v info, -vv debug).',
        )
    ] = 0,
    version: Annotated[
        bool | None, typer.Option(
            '--version', callback=_version_callback, is_eager=True,
            help='Show version and exit.',
        )
    ] = None,
) -> None:
    setup_logging(verbose)


if __name__ == '__main__':
    app()
