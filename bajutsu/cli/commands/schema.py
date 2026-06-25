"""`bajutsu schema` — print the JSON Schema for scenario files (for editor integration)."""

from __future__ import annotations

import typer


def schema() -> None:
    """Print the JSON Schema for scenario files (for editor integration)."""
    from bajutsu.lint import scenario_json_schema

    typer.echo(scenario_json_schema())


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(schema)
