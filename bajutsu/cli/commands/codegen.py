"""`bajutsu codegen` — generate a native test from a scenario (no AI; structural mapping)."""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu.cli._shared import DEFAULT_CONFIG, _load_effective
from bajutsu.codegen_emit import EMIT_TARGETS, CodegenError, generate_test
from bajutsu.scenario import load_scenarios


def codegen(
    scenario: str,
    target_name: str = typer.Option(..., "--target"),
    emit: str = typer.Option("xcuitest", "--emit", help="output format (xcuitest | playwright)"),
    out: str = typer.Option("-", "--out", "-o", help="output file, or - for stdout"),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Generate a native test from a scenario (no AI; structural mapping)."""
    if emit not in EMIT_TARGETS:
        typer.echo(f"unsupported --emit: {emit} (one of {', '.join(EMIT_TARGETS)})")
        raise typer.Exit(2)
    eff = _load_effective(config, target_name)
    scenario_path = Path(scenario)
    if not scenario_path.exists():
        typer.echo(f"scenario not found: {scenario}")
        raise typer.Exit(2)
    scenarios = load_scenarios(scenario_path.read_text(encoding="utf-8"))
    stem = Path(out).stem if out != "-" else scenario_path.stem
    try:
        code, _filename = generate_test(emit, scenarios, stem, eff)
    except CodegenError as exc:
        # The CLI keeps its own web-target hint, which names the config key to set.
        if emit == "playwright":
            typer.echo(f"--emit playwright needs targets.{target_name}.baseUrl (a web target)")
        else:
            typer.echo(str(exc))
        raise typer.Exit(2) from exc
    if out == "-":
        typer.echo(code)
    else:
        Path(out).write_text(code, encoding="utf-8")
        typer.echo(f"wrote {len(scenarios)} scenario(s) -> {out}")


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(codegen)
