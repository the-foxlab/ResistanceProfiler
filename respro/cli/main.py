"""
CLI entry point for ResistanceProfiler.

Commands:
- respro init             — initialise a GenBank-backed project database
- respro add              — add rules and optional GenBank annotations to an existing project
- respro vcf              — run resistance profiling on a VCF file
- respro fasta            — run resistance profiling on a consensus FASTA
- respro manage           — inspect project/results databases and delete stored runs
- respro regenerate       — regenerate a report from a stored run
- respro classify         — add manual classification data to a stored run
- respro maintained.db    — browse and download databases from the companion repository
- respro manage results --sync — sync stored run annotations with a project database
"""

from __future__ import annotations

from typing import Annotated

import typer

from respro import __version__
from respro.cli import classify as _classify_module
from respro.cli import explore as _explore_module
from respro.cli import fasta as _fasta_module
from respro.cli import init as _init_module
from respro.cli import maintained_db as _maintained_db_module
from respro.cli import regenerate as _regenerate_module
from respro.cli import vcf as _vcf_module
from respro.utils.logging import setup_logging

app = typer.Typer(
    help='ResistanceProfiler — agnostic antiviral resistance profiling framework.',
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    context_settings={'help_option_names': ['-h', '--help']},
)

_maintained_db_module.register(app)
_init_module.register(app)
_vcf_module.register(app)
_fasta_module.register(app)
_explore_module.register(app)
_regenerate_module.register(app)
_classify_module.register(app)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f'respro {__version__}')
        raise typer.Exit()


@app.callback()
def _callback(
    verbose: Annotated[
        int, typer.Option(
            '--verbose',
            '-v',
            count=True,
            metavar='',
            show_default=False,
            help='Increase verbosity (-v info, -vv debug).',
        )
    ] = 0,
    version: Annotated[
        bool | None, typer.Option(
            '--version',
            callback=_version_callback,
            is_eager=True,
            help='Show version and exit.',
        )
    ] = None,
) -> None:
    setup_logging(verbose)


if __name__ == '__main__':
    app()
