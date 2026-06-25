"""`bajutsu audit` — score a scenario's determinism (BE-0049).

Two modes, both read-only and advisory — they never decide a verdict and never gate CI:

- **Static (default)**: no device, no AI. Grade selectors on the stability ladder, flag over-loose
  waits and coordinate gestures.
- **Repeat-and-diff** (`--repeat K --target <app>`): run the scenario K times under identical
  preconditions and report anything whose outcome varied (`deterministic` vs `flaky`). This is the
  *opposite* of flakiness tolerance / auto-retry — a divergence is a finding to fix, not a retry
  that turns red into green.

A successful audit exits 0 *even with findings*; only a missing / unreadable scenario file (or, for
`--repeat`, an unavailable target / backend) exits 2.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from bajutsu import audit as _audit
from bajutsu import env as _env
from bajutsu.backends import ensure_web_runtime, select_actuator
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective, load_expanded_scenarios
from bajutsu.runner import device_pool, run_all
from bajutsu.scenario import Scenario


def audit(
    scenario: str = typer.Argument(..., help="scenario file to audit"),
    as_json: bool = typer.Option(False, "--json", help="emit the reports as JSON instead of text"),
    repeat: int = typer.Option(
        0, "--repeat", "-k", help="run the scenario K times and diff outcomes (K>=2); 0 = static"
    ),
    target_name: str = typer.Option(
        "", "--target", help="app/target to run against (for --repeat)"
    ),
    backend: str = typer.Option("", "--backend"),
    udid: str = typer.Option("booted", "--udid"),
    config: str = typer.Option(DEFAULT_CONFIG, "--config"),
) -> None:
    """Score a scenario's determinism — statically, or by repeated execution with `--repeat`.

    Read-only and advisory: it never decides a verdict and never gates CI. A successful audit exits
    0 even with findings; a missing / unreadable scenario file (or, for `--repeat`, an unavailable
    target / backend) exits 2.
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

    if repeat >= 2:
        _repeat_audit(scenarios, repeat, target_name, backend, udid, config, as_json)
        return

    reports = [_audit.audit_scenario(s) for s in scenarios]
    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(r) for r in reports], indent=2))
    else:
        typer.echo("\n\n".join(_audit.render(r) for r in reports))


def _repeat_audit(
    scenarios: list[Scenario],
    repeat: int,
    target_name: str,
    backend: str,
    udid: str,
    config: str,
    as_json: bool,
) -> None:
    """Run each scenario `repeat` times through a real device pool and diff the outcomes."""
    if not target_name:
        typer.echo("--repeat needs --target (the app to run the scenario against)")
        raise typer.Exit(2)
    eff = _load_effective(config, target_name)  # exits 2 on missing config / unknown target
    backends = _backends(backend, eff.backend)
    # Mirror `run`/`doctor`: validate the backend before touching device CLIs, so an unknown /
    # unavailable actuator exits cleanly instead of crashing later.
    try:
        ensure_web_runtime(backends)
        actuator = select_actuator(backends)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    udids = ["web"] if actuator == "playwright" else [_env.resolve_udid(udid)]

    run_id = datetime.now(tz=UTC).strftime("audit-%Y%m%d-%H%M%S")
    lease, shutdown = device_pool(udids, backends, eff, Path("runs") / run_id)
    try:
        # One scenario at a time, K times each (workers=1 keeps the K runs under identical
        # conditions — the point of the diff). run_dir=None: the audit compares outcomes, not
        # artifacts, so it doesn't write a per-run report tree.
        reports = [
            _audit.repeat_diff(run_all(eff, [s] * repeat, lease, workers=1, actuator=actuator))
            for s in scenarios
        ]
    finally:
        shutdown()

    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(r) for r in reports], indent=2))
    else:
        typer.echo("\n\n".join(_audit.render_repeat(r) for r in reports))


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(audit)
