"""`bajutsu lint` — validate a scenario file without running it."""

from __future__ import annotations

from pathlib import Path

import typer


def lint(
    scenario: str = typer.Argument(..., help="Path to a scenario *.yaml file"),
) -> None:
    """Validate a scenario file without running it."""
    from bajutsu.lint import lint_file

    errors = lint_file(Path(scenario))
    if errors:
        for e in errors:
            typer.echo(e)
        raise typer.Exit(1)
    typer.echo("ok")


def register(app: typer.Typer) -> None:
    app.command()(lint)
