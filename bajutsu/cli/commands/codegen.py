"""`bajutsu codegen` — generate a native test from a scenario (no AI; structural mapping)."""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu.cli._shared import DEFAULT_CONFIG, _load_effective
from bajutsu.codegen import class_name_for, to_xcuitest
from bajutsu.scenario import load_scenarios


def codegen(
    scenario: str,
    app_name: str = typer.Option(..., "--app"),
    emit: str = typer.Option("xcuitest", "--emit", help="output format (xcuitest)"),
    out: str = typer.Option("-", "--out", "-o", help="output file, or - for stdout"),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Generate a native test from a scenario (no AI; structural mapping)."""
    if emit != "xcuitest":
        typer.echo(f"unsupported --emit: {emit} (only 'xcuitest')")
        raise typer.Exit(2)
    eff = _load_effective(config, app_name)
    scenario_path = Path(scenario)
    if not scenario_path.exists():
        typer.echo(f"scenario not found: {scenario}")
        raise typer.Exit(2)
    scenarios = load_scenarios(scenario_path.read_text(encoding="utf-8"))
    stem = Path(out).stem if out != "-" else scenario_path.stem
    code = to_xcuitest(scenarios, class_name_for(stem), eff.launch_env)
    if out == "-":
        typer.echo(code)
    else:
        Path(out).write_text(code, encoding="utf-8")
        typer.echo(f"wrote {len(scenarios)} scenario(s) -> {out}")


def register(app: typer.Typer) -> None:
    app.command()(codegen)
