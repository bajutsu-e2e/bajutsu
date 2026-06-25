"""`bajutsu lint` — validate a scenario file without running it."""

from __future__ import annotations

from pathlib import Path

import typer


def lint(
    scenario: str = typer.Argument(..., help="Path to a scenario *.yaml file"),
) -> None:
    """Validate a scenario file without running it."""
    from bajutsu.lint import lint_text, provenance_coverage
    from bajutsu.scenario import load_scenario_file

    path = Path(scenario)
    if not path.exists():
        typer.echo(f"file not found: {path}")
        raise typer.Exit(1)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        typer.echo(f"read error: {e}")
        raise typer.Exit(1) from e
    # Read the file once and reuse that text for both the validation and the advisory, so the two
    # never see different content (a file edited between two reads).
    errors = lint_text(text)
    if errors:
        for msg in errors:
            typer.echo(msg)
        raise typer.Exit(1)
    typer.echo("ok")
    # Advisory only (BE-0044): report `from:` provenance coverage; never fails the lint.
    advisory = provenance_coverage(load_scenario_file(text).scenarios)
    if advisory is not None:
        typer.echo(advisory)


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(lint)
