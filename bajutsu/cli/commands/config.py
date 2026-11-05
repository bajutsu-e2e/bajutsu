"""`bajutsu config schema` — print the JSON Schema for `bajutsu.config.yaml` (BE-0452)."""

from __future__ import annotations

import json

import typer

config_app = typer.Typer(help="Inspect the config file format.", no_args_is_help=True)


@config_app.command("schema")
def schema() -> None:
    """Print the JSON Schema for config files (for editor integration).

    Generated from the config models and the platform registry: each target is a `oneOf` keyed by
    its `platform`, so an editor completes the keys of the platform written.
    """
    from bajutsu.common.config.json_schema import config_json_schema

    typer.echo(json.dumps(config_json_schema(), indent=2, ensure_ascii=False))


def register(app: typer.Typer) -> None:
    """Register the `config` command group on the Typer app."""
    app.add_typer(config_app, name="config")
