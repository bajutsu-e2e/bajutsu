"""`bajutsu audit` — statically score a scenario's determinism (no device, no AI, BE-0049).

Read-only and advisory: it grades selectors on the stability ladder, flags over-loose waits and
coordinate gestures, and reports findings. It never runs the scenario and never gates CI — a
successful audit exits 0 *even with findings* (only a missing / unreadable scenario file exits 2),
so it strengthens determinism-first without ever deciding a verdict.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import typer

from bajutsu import audit as _audit
from bajutsu.cli._shared import load_expanded_scenarios


def audit(
    scenario: str = typer.Argument(..., help="scenario file to audit"),
    as_json: bool = typer.Option(False, "--json", help="emit the reports as JSON instead of text"),
) -> None:
    """Statically score a scenario's determinism.

    Selector stability, loose waits, coordinate gestures. Read-only and advisory: it never runs the
    scenario and never gates CI — a successful audit exits 0 even with findings; only a missing /
    unreadable scenario file exits 2.
    """
    path = Path(scenario)
    if not path.is_file():
        typer.echo(f"scenario not found: {scenario}")
        raise typer.Exit(2)
    try:
        scenarios = load_expanded_scenarios(path)
    except (OSError, ValueError) as e:
        typer.echo(f"failed to load scenario: {e}")
        raise typer.Exit(2) from None

    reports = [_audit.audit_scenario(s) for s in scenarios]
    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(r) for r in reports], indent=2))
    else:
        typer.echo("\n\n".join(_audit.render(r) for r in reports))


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(audit)
