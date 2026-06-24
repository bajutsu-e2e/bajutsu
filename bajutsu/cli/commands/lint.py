"""`bajutsu lint` — validate a scenario file without running it."""

from __future__ import annotations

from pathlib import Path

import typer


def lint(
    scenario: str = typer.Argument(..., help="Path to a scenario *.yaml file"),
) -> None:
    """Validate a scenario file without running it."""
    from bajutsu.lint import lint_file, provenance_coverage
    from bajutsu.scenario import load_scenario_file

    path = Path(scenario)
    errors = lint_file(path)
    if errors:
        for e in errors:
            typer.echo(e)
        raise typer.Exit(1)
    typer.echo("ok")
    # Advisory only (BE-0044): report `from:` provenance coverage; never fails the lint.
    advisory = provenance_coverage(load_scenario_file(path.read_text(encoding="utf-8")).scenarios)
    if advisory is not None:
        typer.echo(advisory)


def register(app: typer.Typer) -> None:
    app.command()(lint)
