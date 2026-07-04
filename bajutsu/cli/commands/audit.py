"""`bajutsu audit` — score a scenario's determinism (BE-0049).

Three modes, all read-only and advisory — they never decide a verdict and never gate CI:

- **Static (default)**: no device, no AI. Grade selectors on the stability ladder, flag over-loose
  waits and coordinate gestures.
- **Repeat-and-diff** (`--repeat K --target <app>`): run the scenario K times under identical
  preconditions and report anything whose outcome varied (`deterministic` vs `flaky`). This is the
  *opposite* of flakiness tolerance / auto-retry — a divergence is a finding to fix, not a retry
  that turns red into green.
- **Longitudinal** (`--history <runs-dir>`): mine accumulated runs, grouping each scenario's
  outcomes by its `provenance.scenarioHash`, and classify any whose verdict flipped at a constant
  fingerprint as flaky. No device, no AI.

A successful audit exits 0 *even with findings*; only a missing / unreadable input (scenario file or
runs dir; or, for `--repeat`, an unavailable target / backend) exits 2.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from bajutsu import audit as _audit
from bajutsu import simctl as _simctl
from bajutsu.backends import ensure_web_runtime, select_actuator
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective, load_expanded_scenarios
from bajutsu.runner import device_pool, run_all
from bajutsu.runner.launch_server import start_launch_server
from bajutsu.scenario import Scenario


def audit(
    scenario: str = typer.Argument("", help="scenario file to audit (omit with --history)"),
    as_json: bool = typer.Option(False, "--json", help="emit the reports as JSON instead of text"),
    repeat: int = typer.Option(
        0, "--repeat", "-k", help="run the scenario K times and diff outcomes (K>=2); 0 = static"
    ),
    history: str = typer.Option(
        "",
        "--history",
        help="dir of past runs to mine for flakiness, grouping by scenario fingerprint",
    ),
    target_name: str = typer.Option(
        "", "--target", help="app/target to run against (for --repeat)"
    ),
    backend: str = typer.Option("", "--backend"),
    udid: str = typer.Option("booted", "--udid"),
    config: str = typer.Option(DEFAULT_CONFIG, "--config"),
) -> None:
    """Score a scenario's determinism — statically, by repeated execution, or over run history.

    Three read-only, advisory modes: the static audit (default), repeat-and-diff (`--repeat`), and
    the longitudinal view (`--history <runs-dir>`, which groups accumulated runs by scenario
    fingerprint and flags any whose verdict flipped). None decides a verdict or gates CI. A
    successful audit exits 0 even with findings; a missing input (scenario file, runs dir) or an
    unavailable `--repeat` target / backend exits 2.
    """
    if history:
        if repeat:
            # The longitudinal view reads past runs; --repeat executes new ones. Asking for both is a
            # usage error rather than silently dropping one.
            typer.echo("--history reads past runs and can't be combined with --repeat")
            raise typer.Exit(2)
        if scenario:
            # --history mines a whole runs dir; a positional scenario can't filter it, so taking both
            # is a usage error rather than silently ignoring the scenario the user passed.
            typer.echo("--history mines past runs and takes no scenario argument; omit it")
            raise typer.Exit(2)
        _history_audit(history, as_json)
        return

    if not scenario:
        typer.echo(
            "audit needs a scenario file (or --history <runs-dir> for the longitudinal view)"
        )
        raise typer.Exit(2)
    path = Path(scenario)
    if not path.is_file():
        typer.echo(f"scenario not found: {scenario}")
        raise typer.Exit(2)
    try:
        scenarios = load_expanded_scenarios(path)
    except (OSError, ValueError) as e:
        typer.echo(f"failed to load scenario: {e}")
        raise typer.Exit(2) from None

    if repeat and repeat < 2:
        # One run can't be diffed against anything, so a single (or negative) repeat is a usage
        # error rather than a silent fall-through to the static audit.
        typer.echo(
            "--repeat needs K>=2 (one run can't be compared); omit --repeat for the static audit"
        )
        raise typer.Exit(2)
    if repeat >= 2:
        _repeat_audit(scenarios, repeat, target_name, backend, udid, config, as_json)
        return

    reports = [_audit.audit_scenario(s) for s in scenarios]
    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(r) for r in reports], indent=2))
    else:
        typer.echo("\n\n".join(_audit.render(r) for r in reports))


def _history_audit(history: str, as_json: bool) -> None:
    """Mine a runs directory for flakiness — group runs by scenario fingerprint and classify each."""
    runs_dir = Path(history)
    if not runs_dir.is_dir():
        typer.echo(f"runs directory not found: {history}")
        raise typer.Exit(2)
    report = _audit.longitudinal(_read_manifests(runs_dir))
    if as_json:
        typer.echo(json.dumps(dataclasses.asdict(report), indent=2))
    else:
        typer.echo(_audit.render_longitudinal(report))


def _read_manifests(runs_dir: Path) -> list[dict[str, Any]]:
    """The parsed `manifest.json` of each run under *runs_dir*; unreadable/malformed ones are skipped.

    A run that can't be parsed carries no usable provenance, so dropping it here is equivalent to its
    being skipped for lacking a fingerprint — the audit is advisory and never gates on completeness.
    """
    manifests: list[dict[str, Any]] = []
    for d in sorted(runs_dir.iterdir()):
        manifest = d / "manifest.json"
        if not (d.is_dir() and manifest.is_file()):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            manifests.append(data)
    return manifests


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
        ensure_web_runtime(backends, eff.browser)
        actuator = select_actuator(backends)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    try:
        udids = ["web"] if actuator == "playwright" else [_simctl.resolve_udid(udid)]
    except _simctl.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None

    run_id = datetime.now(tz=UTC).strftime("audit-%Y%m%d-%H%M%S")
    lease, shutdown = device_pool(udids, backends, eff, Path("runs") / run_id)
    # Bring up the target's `launchServer` (the web baseUrl host) if declared, like `run` does, so a
    # web target with a server-backed baseUrl can be audited; reused if already serving, torn down
    # in the finally below.
    try:
        # Audit is a CLI-only longitudinal tool; serve never spawns it for an uploaded bundle, so it
        # stays ungoverned (upload_exec=None — today's bare-host path).
        stop_server, _exec_decision = start_launch_server(eff)
    except RuntimeError as e:
        typer.echo(str(e))
        shutdown()
        raise typer.Exit(2) from None
    try:
        # One scenario at a time, K times each (workers=1 keeps the K runs under identical
        # conditions — the point of the diff). run_dir=None: the audit compares outcomes, not
        # artifacts, so it doesn't write a per-run report tree.
        reports = [
            _audit.repeat_diff(run_all(eff, [s] * repeat, lease, workers=1, actuator=actuator))
            for s in scenarios
        ]
    except _simctl.DeviceError as e:
        # Parity with `run`/`record`: a device failure (lease/launch) exits 2 cleanly, not a traceback.
        typer.echo(str(e))
        raise typer.Exit(2) from None
    finally:
        shutdown()
        stop_server()

    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(r) for r in reports], indent=2))
    else:
        typer.echo("\n\n".join(_audit.render_repeat(r) for r in reports))


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(audit)
