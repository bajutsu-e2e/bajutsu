"""`bajutsu coverage` — statically map a suite's id-namespace coverage (no device, no AI, BE-0050).

Read-only and advisory: it walks every scenario in the app's configured `scenarios` dir, groups the
stable ids they reference by namespace, and measures them against the app's declared `idNamespaces`
— reporting per-namespace coverage, the gap list, and off-namespace ids. It never runs a scenario
and never gates CI: a successful coverage report exits 0 *even with gaps* (only a missing config /
scenarios dir or an unreadable scenario exits 2), so it strengthens app-readiness insight without
ever deciding a verdict.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import typer

from bajutsu import coverage as _coverage
from bajutsu.cli._shared import DEFAULT_CONFIG, _load_effective, load_expanded_scenarios


def coverage(
    app_name: str = typer.Option(..., "--app"),
    config: str = typer.Option(DEFAULT_CONFIG),
    as_json: bool = typer.Option(False, "--json", help="emit the report as JSON instead of text"),
) -> None:
    """Statically map which of the app's declared id namespaces its scenario suite touches —
    per-namespace coverage, the gap list (untested namespaces), and off-namespace ids. Read-only
    and advisory: it never runs a scenario and never gates CI — it exits 0 even with gaps; only a
    missing config / scenarios dir or an unreadable scenario exits 2."""
    eff = _load_effective(config, app_name)
    if eff.scenarios is None:
        typer.echo(f"app '{app_name}' has no scenarios dir (set apps.{app_name}.scenarios)")
        raise typer.Exit(2)
    scenarios_dir = Path(eff.scenarios)
    if not scenarios_dir.is_dir():
        typer.echo(f"scenarios dir not found: {eff.scenarios}")
        raise typer.Exit(2)
    try:
        scenarios = [
            s for f in sorted(scenarios_dir.glob("*.yaml")) for s in load_expanded_scenarios(f)
        ]
    except (OSError, ValueError) as e:
        typer.echo(f"failed to load scenarios: {e}")
        raise typer.Exit(2) from None

    report = _coverage.coverage(scenarios, eff.id_namespaces)
    if as_json:
        typer.echo(json.dumps(dataclasses.asdict(report), indent=2))
    else:
        typer.echo(_coverage.render(report))


def register(app: typer.Typer) -> None:
    app.command()(coverage)
