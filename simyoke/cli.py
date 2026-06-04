"""Simyoke CLI. Per-app differences come from config; the runner is shared."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer

from simyoke.backends import make_driver, select_actuator
from simyoke.claude_agent import ClaudeAgent
from simyoke.config import Effective, load_config, resolve
from simyoke.doctor import render, score
from simyoke.record import record as record_loop
from simyoke.runner import device_factory, launch_driver, run_and_report
from simyoke.scenario import dump_scenarios, load_scenarios

app = typer.Typer(add_completion=False, help="自然言語駆動 iOS E2E テストツール（Simulator 限定）")

DEFAULT_CONFIG = "simyoke.config.yaml"


def _load_effective(config: str, app_name: str) -> Effective:
    cfg_path = Path(config)
    if not cfg_path.exists():
        typer.echo(f"config not found: {config}")
        raise typer.Exit(2)
    cfg = load_config(cfg_path.read_text(encoding="utf-8"))
    try:
        return resolve(cfg, app_name)
    except KeyError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None


def _backends(backend: str, fallback: list[str]) -> list[str]:
    return [b.strip() for b in backend.split(",") if b.strip()] if backend else fallback


@app.command()
def run(
    scenario: str,
    app_name: str = typer.Option(..., "--app"),
    backend: str = typer.Option("", help="comma list; first available is the actuator"),
    udid: str = typer.Option("booted"),
    workers: int = typer.Option(1),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Run a scenario deterministically (no AI)."""
    eff = _load_effective(config, app_name)
    scenario_path = Path(scenario)
    if not scenario_path.exists():
        typer.echo(f"scenario not found: {scenario}")
        raise typer.Exit(2)
    scenarios = load_scenarios(scenario_path.read_text(encoding="utf-8"))
    try:
        factory = device_factory(udid, _backends(backend, eff.backend))
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    results, manifest = run_and_report(eff, scenarios, factory, Path("runs"), run_id)
    ok = all(r.ok for r in results)
    typer.echo(f"{'PASS' if ok else 'FAIL'}  {manifest}")
    raise typer.Exit(0 if ok else 1)


@app.command()
def record(
    out: str,
    app_name: str = typer.Option(..., "--app"),
    goal: str = typer.Option(..., "--goal", help="natural-language goal to author"),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Explore the app with AI toward a goal and write the recorded scenario to OUT."""
    eff = _load_effective(config, app_name)
    try:
        actuator = select_actuator(_backends(backend, eff.backend))
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    driver = launch_driver(udid, eff, actuator)
    scenario = record_loop(driver, goal, ClaudeAgent(), name=goal)
    Path(out).write_text(dump_scenarios([scenario]), encoding="utf-8")
    typer.echo(f"recorded {len(scenario.steps)} steps -> {out}")


@app.command()
def doctor(
    app_name: str = typer.Option(..., "--app"),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Report a convention score for the app's current screen."""
    eff = _load_effective(config, app_name)
    try:
        actuator = select_actuator(_backends(backend, eff.backend))
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    driver = make_driver(actuator, udid)
    result = score(driver.query(), eff.id_namespaces)
    typer.echo(render(result))
    raise typer.Exit(0 if result.grade != "Blocked" else 1)


if __name__ == "__main__":
    app()
