"""`bajutsu record` — explore the app with AI toward a goal and author a scenario."""

from __future__ import annotations

import atexit
from dataclasses import replace
from pathlib import Path

import typer

from bajutsu import env as _env
from bajutsu import usage as _usage
from bajutsu.agents import make_agent, resolve_kind
from bajutsu.backends import ensure_web_runtime, select_actuator
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective
from bajutsu.config import Effective
from bajutsu.record import record as record_loop
from bajutsu.runner import launch_driver
from bajutsu.runner.launch_server import start_launch_server
from bajutsu.scenario import Preconditions, dump_scenarios


def _record_out_path(eff: Effective, out: str, name: str, goal: str, app_name: str) -> Path:
    """Where `record` writes the authored scenario: `--out` when given, else an auto-named,
    never-overwriting `*.yaml` under the app's configured `scenarios` dir (same naming as the
    web UI's Record tab)."""
    if out:
        return Path(out)
    if eff.scenarios is None:
        typer.echo(
            f"app '{app_name}' has no scenarios dir (set apps.{app_name}.scenarios, or pass --out)"
        )
        raise typer.Exit(2)
    from bajutsu.serve import scenario_out_path, unique_scenario_path

    return unique_scenario_path(scenario_out_path(Path(eff.scenarios), name or goal))


def record(
    app_name: str = typer.Option(..., "--app"),
    goal: str = typer.Option(..., "--goal", help="natural-language goal to author"),
    out: str = typer.Option(
        "", "--out", help="explicit output path (overrides the app's configured scenarios dir)"
    ),
    name: str = typer.Option(
        "", "--name", help="file name for the authored scenario (auto-named from the goal if blank)"
    ),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    erase: bool = typer.Option(
        True, "--erase/--no-erase", help="erase the device before launching (app must be installed)"
    ),
    dismiss_alerts: bool = typer.Option(
        True,
        "--dismiss-alerts/--no-dismiss-alerts",
        help="dismiss unexpected OS prompts while authoring (on by default; uses the same API key)",
    ),
    alert_instruction: str = typer.Option(
        "", "--alert-instruction", help="how to handle a prompt instead of dismissing it"
    ),
    agent: str = typer.Option(
        "",
        "--agent",
        help="authoring agent: 'api' (Anthropic API, pay-per-token) or 'claude-code' "
        "(the `claude` CLI, billed to your Claude subscription). Defaults to $BAJUTSU_AGENT or 'api'.",
    ),
    headed: bool | None = typer.Option(
        None,
        "--headed/--no-headed",
        help="web backend: author against a visible (headed, slow-motion) browser instead of "
        "headless; default leaves the app's `headless` config",
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Explore the app with AI toward a goal and write the recorded scenario. With `--out` it
    writes there; otherwise it auto-names a file under the app's configured `scenarios` dir."""
    eff = _load_effective(config, app_name)
    # --headed/--no-headed overrides the app's `headless` config (web backend only; iOS ignores it).
    if headed is not None:
        eff = replace(eff, headless=not headed)
    out_path = _record_out_path(eff, out, name, goal, app_name)
    before = _usage.snapshot()
    kind = resolve_kind(agent)
    try:
        authoring_agent = make_agent(kind)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    backends = _backends(backend, eff.backend)
    try:
        ensure_web_runtime(backends)  # auto-install Playwright if a web record needs it
        actuator = select_actuator(backends)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    alert_guard = None
    if dismiss_alerts:
        from bajutsu.alerts import ClaudeAlertLocator, SystemAlertGuard

        alert_guard = SystemAlertGuard(ClaudeAlertLocator(), alert_instruction or None).dismiss
    udid = _env.resolve_udid(udid)

    # Bring up the app's target server (the web baseUrl host) if it declares launchServer — reused
    # if already serving, started otherwise. Stopped when this command exits (atexit).
    try:
        stop_server = start_launch_server(eff)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    atexit.register(stop_server)

    # Narrate the otherwise-silent device work (reinstall + boot + launch) so the watcher
    # knows what's happening before the agent takes over. Progress goes to stderr, like the
    # record loop's own stream, leaving stdout for the final result line.
    def say(msg: str) -> None:
        typer.echo(msg, err=True)

    say(
        f"⚙️  preparing the simulator — installing and launching {app_name} (this can take a moment) …"
    )
    try:
        driver = launch_driver(udid, eff, actuator, Preconditions(erase=erase))
    except _env.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    say(f"✅ app is up — authoring toward the goal: {goal!r}")
    scenario = record_loop(
        driver,
        goal,
        authoring_agent,
        name=goal,
        alert_guard=alert_guard,
        report=say,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dump_scenarios([scenario]), encoding="utf-8")
    typer.echo(f"recorded {len(scenario.steps)} steps ({kind} agent) -> {out_path}")
    # Report what the authoring (and alert-guard) AI consumed; to stderr, like the progress
    # narration, so stdout stays the single result line. The claude-code agent bills no tokens
    # here, so its delta is empty and nothing is shown.
    spent = _usage.snapshot() - before
    if spent.calls:
        typer.echo(spent.render(), err=True)


def register(app: typer.Typer) -> None:
    app.command()(record)
