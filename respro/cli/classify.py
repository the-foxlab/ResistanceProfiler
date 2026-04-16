"""
`respro classify` command — set a manual sample classification for a stored run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console

from respro.db.results import (
    load_run,
    save_classification,
)
from respro.db.schema import open_results_db


def classify(
    result_db: Annotated[
        Path,
        typer.Option(
            '--results-db',
            '-d',
            exists=True,
            help='Results database.',
        ),
    ],
    run_id: Annotated[
        int,
        typer.Option(
            '--run-id',
            '-i',
            help='Run ID to classify.',
        ),
    ],
    drug: Annotated[
        str,
        typer.Option(
            '--drug',
            help='Drug name this classification applies to.',
        ),
    ],
    phenotype: Annotated[
        str | None,
        typer.Option(
            '--phenotype',
            help='Resistance phenotype (resistant / intermediate / sensitive / unknown).',
        ),
    ] = None,
    clinical_phenotype: Annotated[
        str | None,
        typer.Option(
            '--clinical-phenotype',
            help='Externally verified clinical phenotype.',
        ),
    ] = None,
    ic50: Annotated[
        str,
        typer.Option(
            '--ic50',
            help='IC50 value string.',
        ),
    ] = '',
    fold_ic50: Annotated[
        str,
        typer.Option(
            '--fold-ic50',
            help='Fold-IC50 value string.',
        ),
    ] = '',
    note: Annotated[
        str,
        typer.Option(
            '--note',
            help='Free-text note.',
        ),
    ] = '',
    source: Annotated[
        str,
        typer.Option(
            '--source',
            help='Source or reference for this classification.',
        ),
    ] = '',
) -> None:
    """Set one manual sample classification for a stored run."""
    if not any([phenotype, clinical_phenotype, ic50, fold_ic50]):
        raise click.UsageError(
            'At least one of --phenotype, --clinical-phenotype, --ic50, or --fold-ic50 must be provided.'
        )

    console = Console(highlight=False)
    results_conn = None
    try:
        results_conn = open_results_db(result_db)
        # Verify run exists.
        load_run(results_conn, run_id)
        save_classification(
            results_conn,
            run_id,
            drug=drug,
            phenotype=phenotype or 'unknown',
            clinical_phenotype=clinical_phenotype or 'unknown',
            ic50=ic50,
            fold_ic50=fold_ic50,
            note=note,
            source=source,
        )
        console.print(
            f'[green]✓[/green] Classification saved for run #{run_id}.'
        )
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if results_conn is not None:
            results_conn.close()


def register(app: typer.Typer) -> None:
    """Register the classify command on the given Typer app."""
    app.command()(classify)
