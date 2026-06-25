"""`bajutsu codegen` — generate a native test from a scenario (no AI; structural mapping)."""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu.cli._shared import DEFAULT_CONFIG, _load_effective
from bajutsu.codegen import class_name_for, to_xcuitest
from bajutsu.codegen_playwright import describe_name_for, to_playwright
from bajutsu.scenario import load_scenarios

_EMIT_TARGETS = ("xcuitest", "playwright")


def codegen(
    scenario: str,
    target_name: str = typer.Option(..., "--target"),
    emit: str = typer.Option("xcuitest", "--emit", help="output format (xcuitest | playwright)"),
    out: str = typer.Option("-", "--out", "-o", help="output file, or - for stdout"),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Generate a native test from a scenario (no AI; structural mapping)."""
    if emit not in _EMIT_TARGETS:
        typer.echo(f"unsupported --emit: {emit} (one of {', '.join(_EMIT_TARGETS)})")
        raise typer.Exit(2)
    eff = _load_effective(config, target_name)
    scenario_path = Path(scenario)
    if not scenario_path.exists():
        typer.echo(f"scenario not found: {scenario}")
        raise typer.Exit(2)
    scenarios = load_scenarios(scenario_path.read_text(encoding="utf-8"))
    stem = Path(out).stem if out != "-" else scenario_path.stem
    if emit == "playwright":
        if not eff.base_url:
            typer.echo(f"--emit playwright needs targets.{target_name}.baseUrl (a web target)")
            raise typer.Exit(2)
        code = to_playwright(scenarios, describe_name_for(stem), eff.base_url, eff.launch_env)
    else:
        code = to_xcuitest(scenarios, class_name_for(stem), eff.launch_env)
    if out == "-":
        typer.echo(code)
    else:
        Path(out).write_text(code, encoding="utf-8")
        typer.echo(f"wrote {len(scenarios)} scenario(s) -> {out}")


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(codegen)
