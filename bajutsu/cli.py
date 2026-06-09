"""Bajutsu CLI. Per-app differences come from config; the runner is shared."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import typer

from bajutsu import env as _env
from bajutsu import github, preflight
from bajutsu import trace as _trace
from bajutsu import triage as _triage
from bajutsu.backends import make_driver, select_actuator
from bajutsu.claude_agent import ClaudeAgent
from bajutsu.codegen import class_name_for, to_xcuitest
from bajutsu.config import Effective, load_config, resolve
from bajutsu.doctor import render, score
from bajutsu.dotenv import load_dotenv
from bajutsu.evidence import FileSink
from bajutsu.network import NetworkCollector
from bajutsu.record import record as record_loop
from bajutsu.runner import (
    device_factory,
    device_pool,
    device_relauncher,
    device_teardown,
    launch_driver,
    run_and_report,
)
from bajutsu.scenario import (
    Preconditions,
    apply_setups,
    dump_mocks,
    dump_scenarios,
    expand_components,
    expand_data,
    load_component,
    load_scenarios,
    read_csv,
    select_scenarios,
)

app = typer.Typer(add_completion=False, help="自然言語駆動 iOS E2E テストツール（Simulator 限定）")

DEFAULT_CONFIG = "bajutsu.config.yaml"


@app.callback()
def _bootstrap() -> None:
    """Load a gitignored .env (e.g. ANTHROPIC_API_KEY) before any command runs."""
    load_dotenv()


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
    tag: str = typer.Option("", "--tag", help="comma list; run only scenarios with any of these tags"),
    exclude: str = typer.Option("", "--exclude", help="comma list; skip scenarios with any of these tags"),
    udid: str = typer.Option("booted"),
    workers: int = typer.Option(1),
    erase: bool = typer.Option(True, "--erase/--no-erase", help="erase the device before each test"),
    dismiss_alerts: bool = typer.Option(
        False, "--dismiss-alerts", help="use vision to dismiss unexpected OS prompts (needs API key)"
    ),
    alert_instruction: str = typer.Option(
        "", "--alert-instruction", help="how to handle a prompt instead of dismissing it"
    ),
    log_predicate: str = typer.Option(
        "", "--log-predicate", help="NSPredicate narrowing the deviceLog stream (e.g. subsystem)"
    ),
    log_subsystem: str = typer.Option(
        "", "--log-subsystem", help="os_log subsystem for appTrace (defaults to the app's bundleId)"
    ),
    network: bool = typer.Option(
        True, "--network/--no-network",
        help="collect the app's network exchanges (for `request` assertions); needs BajutsuKit in the app",
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Run a scenario deterministically (no AI, unless --dismiss-alerts)."""
    eff = _load_effective(config, app_name)
    # Resolve declared secrets from the environment. They reach the device as ${secrets.X}
    # is interpolated at action time, while their literal values are masked in evidence and
    # run-level artifacts (the scenario definition keeps the token, never the value).
    secret_bindings = {f"secrets.{n}": os.environ[n] for n in eff.secrets if n in os.environ}
    secret_values = list(secret_bindings.values())
    scenario_path = Path(scenario)
    if not scenario_path.exists():
        typer.echo(f"scenario not found: {scenario}")
        raise typer.Exit(2)
    scenarios = load_scenarios(scenario_path.read_text(encoding="utf-8"))
    include = [t.strip() for t in tag.split(",") if t.strip()]
    excluded = [t.strip() for t in exclude.split(",") if t.strip()]
    if include or excluded:
        scenarios = select_scenarios(scenarios, include, excluded)
        if not scenarios:
            typer.echo("no scenarios match --tag/--exclude")
            raise typer.Exit(2)
    # Prepend any reusable setup prelude (its steps run before the scenario's own). The
    # setup reference is a scenario file resolved relative to this scenario's directory.
    base_dir = scenario_path.parent
    try:
        apply_setups(
            scenarios, eff.setup,
            lambda ref: load_scenarios((base_dir / ref).read_text(encoding="utf-8"))[0].steps,
        )
    except (OSError, ValueError, IndexError) as e:
        typer.echo(f"setup の読み込みに失敗: {e}")
        raise typer.Exit(2) from None
    # Expand reusable components (`use` steps) into their parameterized steps. Runs after
    # setup expansion so prelude steps may also `use` components.
    try:
        expand_components(
            scenarios,
            lambda ref: load_component((base_dir / ref).read_text(encoding="utf-8")),
        )
    except (OSError, ValueError) as e:
        typer.echo(f"component の展開に失敗: {e}")
        raise typer.Exit(2) from None
    # Data-driven expansion: one run per data row (${row.col} substituted). Must precede
    # the launch-env / collector loops below so every derived scenario is wired up.
    try:
        scenarios = expand_data(
            scenarios,
            lambda ref: read_csv((base_dir / ref).read_text(encoding="utf-8")),
        )
    except (OSError, ValueError) as e:
        typer.echo(f"data の展開に失敗: {e}")
        raise typer.Exit(2) from None
    if not erase:
        for s in scenarios:
            s.preconditions.erase = False
    # Validate the backend before touching the Simulator CLIs, so an unknown/unavailable
    # actuator exits cleanly (2) instead of crashing on a missing `xcrun`/`simctl` (the
    # `run` path mirrors `doctor`: backend check first, then resolve the udid).
    backends = _backends(backend, eff.backend)
    try:
        select_actuator(backends)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    # The idb CLI needs concrete UDIDs (not the simctl "booted" alias). `--udid` may be a
    # comma list — a device pool for parallel runs (`--workers`), capped to the pool size.
    udids = [_env.resolve_udid(u.strip()) for u in udid.split(",") if u.strip()]
    workers = max(1, min(workers, len(udids)))
    parallel = workers > 1
    if parallel and network:
        typer.echo("並列実行（--workers>1）は --no-network が必要（共有コレクタは並列非対応）")
        raise typer.Exit(2)
    on_blocked = None
    if dismiss_alerts:
        from bajutsu.alerts import ClaudeAlertLocator, SystemAlertGuard

        guard = SystemAlertGuard(ClaudeAlertLocator(), alert_instruction or None)
        on_blocked = guard.dismiss
    # Start the network collector and point the app at it via launch env. The app's
    # BajutsuKit POSTs each exchange here (no-op for apps without the SDK).
    collector = None
    if network:
        collector = NetworkCollector()
        url = f"http://127.0.0.1:{collector.start()}"
        for s in scenarios:
            s.preconditions.launch_env.setdefault("BAJUTSU_COLLECTOR", url)
            # Mocks ride the same channel: BajutsuKit stubs matching requests instead of
            # forwarding them (so the network is deterministic, and still observed).
            if s.mocks:
                s.preconditions.launch_env.setdefault("BAJUTSU_MOCKS", dump_mocks(s.mocks))
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    release = None
    if parallel:
        # One driver per device, leased per scenario; interval evidence (video/log) needs a
        # fixed device, so the parallel sink captures instant evidence only (udid=None).
        factory, relauncher, release = device_pool(udids, backends, eff.bundle_id)
        teardown = None
        sink = FileSink(Path("runs") / run_id, redact=eff.redact, secrets=secret_values)
    else:
        factory = device_factory(udids[0], backends)
        relauncher = device_relauncher(udids[0])
        teardown = device_teardown(udids[0])
        sink = FileSink(
            Path("runs") / run_id, udid=udids[0], log_predicate=log_predicate or None,
            log_subsystem=log_subsystem or eff.bundle_id, redact=eff.redact, secrets=secret_values,
        )
    try:
        results, manifest = run_and_report(
            eff, scenarios, factory, Path("runs"), run_id, on_blocked=on_blocked, sink=sink,
            teardown=teardown, collector=collector, relauncher=relauncher,
            workers=workers, release=release, bindings=secret_bindings, secret_values=secret_values,
        )
    finally:
        if collector is not None:
            collector.stop()
    ok = all(r.ok for r in results)
    github.emit(results, manifest.parent / "report.html")  # annotations + summary in CI
    typer.echo(f"{'PASS' if ok else 'FAIL'}  {manifest}")
    raise typer.Exit(0 if ok else 1)


@app.command()
def record(
    out: str,
    app_name: str = typer.Option(..., "--app"),
    goal: str = typer.Option(..., "--goal", help="natural-language goal to author"),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    erase: bool = typer.Option(
        True, "--erase/--no-erase", help="erase the device before launching (app must be installed)"
    ),
    dismiss_alerts: bool = typer.Option(
        False, "--dismiss-alerts", help="dismiss unexpected OS prompts while authoring (needs API key)"
    ),
    alert_instruction: str = typer.Option(
        "", "--alert-instruction", help="how to handle a prompt instead of dismissing it"
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Explore the app with AI toward a goal and write the recorded scenario to OUT."""
    eff = _load_effective(config, app_name)
    try:
        actuator = select_actuator(_backends(backend, eff.backend))
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    alert_guard = None
    if dismiss_alerts:
        from bajutsu.alerts import ClaudeAlertLocator, SystemAlertGuard

        alert_guard = SystemAlertGuard(ClaudeAlertLocator(), alert_instruction or None).dismiss
    udid = _env.resolve_udid(udid)
    driver = launch_driver(udid, eff, actuator, Preconditions(erase=erase))
    scenario = record_loop(driver, goal, ClaudeAgent(), name=goal, alert_guard=alert_guard)
    Path(out).write_text(dump_scenarios([scenario]), encoding="utf-8")
    typer.echo(f"recorded {len(scenario.steps)} steps -> {out}")


@app.command()
def doctor(
    app_name: str = typer.Option(..., "--app"),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Check the environment is runnable, then score the app's current screen."""
    eff = _load_effective(config, app_name)
    try:
        actuator = select_actuator(_backends(backend, eff.backend))
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    # Runnability gate: the CLIs (+ a booted Simulator) the actuator needs. Fail fast here
    # with a fixable checklist instead of crashing later on a missing tool / no device.
    env_checks = preflight.runnability(actuator, booted_count=lambda: len(_env.booted_udids()))
    if env_checks:
        typer.echo("environment:")
        typer.echo(preflight.render(env_checks))
        if not preflight.passed(env_checks):
            raise typer.Exit(1)
        typer.echo("")
    udid = _env.resolve_udid(udid)
    driver = make_driver(actuator, udid)
    result = score(driver.query(), eff.id_namespaces)
    typer.echo(render(result))
    raise typer.Exit(0 if result.grade != "Blocked" else 1)


@app.command()
def trace(
    run_dir: str = typer.Argument("", help="run directory (default: the latest under runs/)"),
    scenario: str = typer.Option("", "--scenario", help="only scenarios whose name contains this"),
    runs: str = typer.Option("runs", help="runs root (used when run_dir is omitted)"),
) -> None:
    """Inspect a finished run as a text timeline (steps + network + appTrace)."""
    path = Path(run_dir) if run_dir else _trace.latest_run(Path(runs))
    if path is None or not (path / "manifest.json").exists():
        typer.echo(f"no run found{f': {run_dir}' if run_dir else f' under {runs}/'}")
        raise typer.Exit(2)
    typer.echo(_trace.trace_run(path, scenario or None))


@app.command()
def triage(
    run_dir: str = typer.Argument("", help="run directory (default: the latest under runs/)"),
    scenario: str = typer.Option("", "--scenario", help="only the scenario whose name contains this"),
    runs: str = typer.Option("runs", help="runs root (used when run_dir is omitted)"),
) -> None:
    """Diagnose a failed run and suggest a minimal fix (advisory — never the pass/fail judge)."""
    path = Path(run_dir) if run_dir else _trace.latest_run(Path(runs))
    if path is None or not (path / "manifest.json").exists():
        typer.echo(f"no run found{f': {run_dir}' if run_dir else f' under {runs}/'}")
        raise typer.Exit(2)
    context = _triage.assemble(path, scenario or None)
    if context is None:
        typer.echo("no failed scenario to triage in this run")
        raise typer.Exit(0)
    result = _triage.HeuristicTriageAgent().triage(context)
    typer.echo(_triage.render(context, result))


@app.command()
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


if __name__ == "__main__":
    app()
