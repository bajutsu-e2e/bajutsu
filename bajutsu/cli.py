"""Bajutsu CLI. Per-app differences come from config; the runner is shared."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import typer

from bajutsu import env as _env
from bajutsu import github, preflight
from bajutsu import trace as _trace
from bajutsu import triage as _triage
from bajutsu.agents import make_agent
from bajutsu.backends import make_driver, select_actuator
from bajutsu.codegen import class_name_for, to_xcuitest
from bajutsu.config import Effective, load_config, resolve
from bajutsu.doctor import render, score
from bajutsu.dotenv import load_dotenv
from bajutsu.record import record as record_loop
from bajutsu.runner import (
    device_pool,
    launch_driver,
    run_and_report,
)
from bajutsu.scenario import (
    DismissAlerts,
    Preconditions,
    Scenario,
    apply_setups,
    dump_mocks,
    dump_scenarios,
    expand_components,
    expand_data,
    load_component,
    load_scenario_file,
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


def _scenario_files(eff: Effective, scenario: str, app_name: str) -> tuple[list[Path], bool]:
    """The scenario files `run` should load: `[--scenario]` when given (an explicit override),
    else every `*.yaml` in the app's configured `scenarios` dir. Returns `(files, single)` where
    `single` flags the one-file override (so the report can carry that file's name/description)."""
    if scenario:
        path = Path(scenario)
        if not path.exists():
            typer.echo(f"scenario not found: {scenario}")
            raise typer.Exit(2)
        return [path], True
    if eff.scenarios is None:
        typer.echo(
            f"app '{app_name}' has no scenarios dir "
            f"(set apps.{app_name}.scenarios, or pass --scenario)"
        )
        raise typer.Exit(2)
    scenarios_dir = Path(eff.scenarios)
    if not scenarios_dir.is_dir():
        typer.echo(f"scenarios dir not found: {eff.scenarios}")
        raise typer.Exit(2)
    files = sorted(scenarios_dir.glob("*.yaml"))
    if not files:
        typer.echo(f"no scenarios found in {eff.scenarios}")
        raise typer.Exit(2)
    return files, False


def _expand_file(path: Path, eff: Effective) -> tuple[list[Scenario], str | None]:
    """Load one scenario file and expand its setup/component/data refs (each resolved relative
    to THIS file's directory, so a multi-file dir run keeps every file's refs local). Returns
    the expanded scenarios plus the file-level description."""
    scenario_file = load_scenario_file(path.read_text(encoding="utf-8"))
    scenarios = scenario_file.scenarios
    # Refs (setup/use/data) resolve relative to this scenario file's own directory.
    base_dir = path.parent
    try:
        apply_setups(
            scenarios,
            eff.setup,
            lambda ref: load_scenarios((base_dir / ref).read_text(encoding="utf-8"))[0].steps,
        )
    except (OSError, ValueError, IndexError) as e:
        typer.echo(f"setup の読み込みに失敗: {e}")
        raise typer.Exit(2) from None
    try:
        expand_components(
            scenarios,
            lambda ref: load_component((base_dir / ref).read_text(encoding="utf-8")),
        )
    except (OSError, ValueError) as e:
        typer.echo(f"component の展開に失敗: {e}")
        raise typer.Exit(2) from None
    try:
        scenarios = expand_data(
            scenarios,
            lambda ref: read_csv((base_dir / ref).read_text(encoding="utf-8")),
        )
    except (OSError, ValueError) as e:
        typer.echo(f"data の展開に失敗: {e}")
        raise typer.Exit(2) from None
    return scenarios, scenario_file.description


def _record_out_path(eff: Effective, out: str, name: str, goal: str, app_name: str) -> Path:
    """Where `record` writes the authored scenario: `--out` when given, else an auto-named,
    never-overwriting `*.yaml` under the app's configured `scenarios` dir (same naming as the
    web UI's Record tab)."""
    if out:
        return Path(out)
    if eff.scenarios is None:
        typer.echo(
            f"app '{app_name}' has no scenarios dir "
            f"(set apps.{app_name}.scenarios, or pass --out)"
        )
        raise typer.Exit(2)
    from bajutsu.serve import scenario_out_path, unique_scenario_path

    return unique_scenario_path(scenario_out_path(Path(eff.scenarios), name or goal))


@app.command()
def run(
    app_name: str = typer.Option(..., "--app"),
    scenario: str = typer.Option(
        "",
        "--scenario",
        help="run only this one *.yaml (overrides the app's configured scenarios dir)",
    ),
    backend: str = typer.Option("", help="comma list; first available is the actuator"),
    tag: str = typer.Option(
        "", "--tag", help="comma list; run only scenarios with any of these tags"
    ),
    exclude: str = typer.Option(
        "", "--exclude", help="comma list; skip scenarios with any of these tags"
    ),
    udid: str = typer.Option("booted"),
    workers: int = typer.Option(1),
    erase: bool | None = typer.Option(
        None,
        "--erase/--no-erase",
        help="override every scenario's preconditions.erase (default: per-scenario)",
    ),
    dismiss_alerts: bool | None = typer.Option(
        None,
        "--dismiss-alerts/--no-dismiss-alerts",
        help="override every scenario's dismissAlerts (default: per-scenario, on; needs API key)",
    ),
    alert_instruction: str = typer.Option(
        "", "--alert-instruction", help="default button instruction (a scenario's own wins)"
    ),
    log_predicate: str = typer.Option(
        "", "--log-predicate", help="NSPredicate narrowing the deviceLog stream (e.g. subsystem)"
    ),
    log_subsystem: str = typer.Option(
        "", "--log-subsystem", help="os_log subsystem for appTrace (defaults to the app's bundleId)"
    ),
    network: bool = typer.Option(
        True,
        "--network/--no-network",
        help="collect the app's network exchanges (for `request` assertions); needs BajutsuKit in the app",
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="stream per-scenario/step progress to stderr as the run advances (used by the web UI)",
    ),
    baselines: str = typer.Option(
        "",
        "--baselines",
        help="directory of baseline images for `visual` assertions "
        "(default: a `baselines` folder beside the scenario)",
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Run a scenario deterministically. Pass/fail is machine-only; the sole AI is the
    alert guard (on by default per scenario), which only fires to clear an OS prompt that
    blocked a step — see each scenario's `dismissAlerts`."""
    eff = _load_effective(config, app_name)
    # Resolve declared secrets from the environment. They reach the device as ${secrets.X}
    # is interpolated at action time, while their literal values are masked in evidence and
    # run-level artifacts (the scenario definition keeps the token, never the value).
    secret_bindings = {f"secrets.{n}": os.environ[n] for n in eff.secrets if n in os.environ}
    secret_values = list(secret_bindings.values())
    # Either the explicit `--scenario` file, or every `*.yaml` in the app's configured dir.
    # Each file's setup/component/data refs resolve relative to its own directory, then the
    # expanded scenarios are concatenated into one run.
    files, single = _scenario_files(eff, scenario, app_name)
    scenarios: list[Scenario] = []
    description: str | None = None
    for path in files:
        expanded, file_desc = _expand_file(path, eff)
        scenarios.extend(expanded)
        if single:
            description = file_desc
    # The report's source label: the single file's name, or the dir name for a config-driven run.
    source_name = files[0].name if single else Path(eff.scenarios or "").name
    # --tag/--exclude selects across the combined, fully-expanded set.
    include = [t.strip() for t in tag.split(",") if t.strip()]
    excluded = [t.strip() for t in exclude.split(",") if t.strip()]
    if include or excluded:
        scenarios = select_scenarios(scenarios, include, excluded)
        if not scenarios:
            typer.echo("no scenarios match --tag/--exclude")
            raise typer.Exit(2)
    # --erase / --no-erase, when given, overrides every scenario; otherwise each scenario's
    # own preconditions.erase (default off) decides whether its device is wiped first.
    if erase is not None:
        for s in scenarios:
            s.preconditions.erase = erase
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
    # --dismiss-alerts / --no-dismiss-alerts, when given, forces every scenario's guard on/off
    # (preserving any per-scenario instruction); otherwise each scenario's own `dismissAlerts`
    # (default on) decides. Mirrors the --erase override.
    if dismiss_alerts is not None:
        for s in scenarios:
            instr = s.dismiss_alerts.instruction if s.dismiss_alerts else None
            s.dismiss_alerts = DismissAlerts(enabled=dismiss_alerts, instruction=instr)
    # Build a per-scenario alert-guard factory unless every scenario disabled it. The vision
    # locator is shared (one Anthropic client); each scenario gets its own guard so its
    # instruction applies. The guard is best-effort, so a missing key just no-ops per run.
    on_blocked_for = None
    if any(s.dismiss_alerts is None or s.dismiss_alerts.enabled for s in scenarios):
        from bajutsu.alerts import ClaudeAlertLocator, SystemAlertGuard
        from bajutsu.orchestrator import BlockedHandler

        if not os.environ.get("ANTHROPIC_API_KEY"):
            typer.echo(
                "note: dismiss-alerts is on but ANTHROPIC_API_KEY is unset — the alert guard will no-op"
            )
        locator = ClaudeAlertLocator()
        default_instruction = alert_instruction or None

        def _guard_for(s: Scenario) -> BlockedHandler | None:
            cfg = s.dismiss_alerts or DismissAlerts()
            if not cfg.enabled:
                return None
            return SystemAlertGuard(locator, cfg.instruction or default_instruction).dismiss

        on_blocked_for = _guard_for
    # Mocks ride the network channel: BajutsuKit stubs matching requests instead of
    # forwarding them (so the network is deterministic, and still observed). They are
    # per-scenario and device-independent, so they're baked into the scenario's launch env;
    # the per-device collector url is injected by the pool at lease time.
    if network:
        for s in scenarios:
            if s.mocks:
                s.preconditions.launch_env.setdefault("BAJUTSU_MOCKS", dump_mocks(s.mocks))
    # Visual assertions resolve `baseline: <name>` within this directory; default to a
    # `baselines` folder beside the scenario(s) so the demo / common case needs no flag.
    baselines_dir = Path(baselines) if baselines else files[0].parent / "baselines"
    run_id = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    # A pool of one-or-more devices. Each device carries its own network collector, evidence
    # sink (interval recordings), and device control — so network collection / video / log /
    # setLocation / push all work the same whether workers is 1 or N.
    lease, shutdown = device_pool(
        udids,
        backends,
        eff,
        Path("runs") / run_id,
        network=network,
        log_predicate=log_predicate or None,
        log_subsystem=log_subsystem or eff.bundle_id,
        secret_values=secret_values,
    )
    # --progress streams scenario/step lines to stderr (the web UI merges them into its run
    # log); stdout stays the machine-readable final PASS/FAIL line.
    progress_fn = (lambda msg: print(msg, file=sys.stderr, flush=True)) if progress else None  # noqa: T201
    try:
        results, manifest = run_and_report(
            eff,
            scenarios,
            lease,
            Path("runs"),
            run_id,
            on_blocked_for=on_blocked_for,
            workers=workers,
            bindings=secret_bindings,
            secret_values=secret_values,
            source_name=source_name,
            description=description,
            progress=progress_fn,
            baselines_dir=baselines_dir,
        )
    except _env.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    finally:
        shutdown()
    ok = all(r.ok for r in results)
    github.emit(results, manifest.parent / "report.html")  # annotations + summary in CI
    typer.echo(f"{'PASS' if ok else 'FAIL'}  {manifest}")
    raise typer.Exit(0 if ok else 1)


@app.command()
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
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Explore the app with AI toward a goal and write the recorded scenario. With `--out` it
    writes there; otherwise it auto-names a file under the app's configured `scenarios` dir."""
    eff = _load_effective(config, app_name)
    out_path = _record_out_path(eff, out, name, goal, app_name)
    kind = agent or os.environ.get("BAJUTSU_AGENT") or "api"
    try:
        authoring_agent = make_agent(kind)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
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
    try:
        driver = launch_driver(udid, eff, actuator, Preconditions(erase=erase))
    except _env.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    scenario = record_loop(
        driver,
        goal,
        authoring_agent,
        name=goal,
        alert_guard=alert_guard,
        report=lambda msg: typer.echo(msg, err=True),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dump_scenarios([scenario]), encoding="utf-8")
    typer.echo(f"recorded {len(scenario.steps)} steps ({kind} agent) -> {out_path}")


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
    scenario: str = typer.Option(
        "", "--scenario", help="only the scenario whose name contains this"
    ),
    runs: str = typer.Option("runs", help="runs root (used when run_dir is omitted)"),
    ai: bool = typer.Option(
        False,
        "--ai",
        help="diagnose with Claude (needs ANTHROPIC_API_KEY) instead of the rule-based agent",
    ),
    apply: str = typer.Option(
        "",
        "--apply",
        help="scenario source file to patch with the suggested fix (shows a dry-run diff)",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="with --apply, write the patched file instead of only showing the diff",
    ),
    rerun: bool = typer.Option(
        False,
        "--rerun",
        help="after --write, re-run the patched scenario to verify the fix (needs --app + a device)",
    ),
    app_name: str = typer.Option("", "--app", help="app key, for --rerun"),
    backend: str = typer.Option("", "--backend", help="actuator backend, for --rerun"),
    udid: str = typer.Option("booted", "--udid", help="simulator udid, for --rerun"),
    config: str = typer.Option(DEFAULT_CONFIG, "--config", help="config path, for --rerun"),
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
    # When patching a source file, diagnose against *its* text (not the run's normalized dump)
    # so a fix's `find` fragment matches the file that --apply edits — otherwise float/flow-style
    # normalization (`timeout: 2.0`, block selectors) makes fragment fixes miss.
    if apply:
        with contextlib.suppress(OSError):
            # _apply_fix reports the read failure below
            context = replace(context, scenario_yaml=Path(apply).read_text(encoding="utf-8"))
    agent: _triage.TriageAgent
    if ai:
        from bajutsu.claude_triage import ClaudeTriageAgent

        agent = ClaudeTriageAgent()
    else:
        agent = _triage.HeuristicTriageAgent()
    result = agent.triage(context)
    typer.echo(_triage.render(context, result))
    if not apply:
        return
    wrote = _apply_fix(result, apply, write)
    if rerun:
        if not wrote:
            typer.echo("\n--rerun needs --write (nothing was applied to re-run).")
        elif not app_name:
            typer.echo("\n--rerun needs --app to run the patched scenario.")
        else:
            _verify_rerun(apply, app_name, backend, udid, config)


def _apply_fix(result: _triage.Triage, target: str, write: bool) -> bool:
    """Render (and optionally write) the suggested fix. Returns True only when a file was written."""
    if result.fix is None:
        typer.echo("\nno applicable structured fix for this failure (advisory only).")
        return False
    try:
        src = Path(target).read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"\ncannot read {target}: {exc}")
        raise typer.Exit(2) from None
    patched, count = _triage.apply_fix(src, result.fix)
    if count == 0:
        typer.echo(
            f"\nfix: {result.fix.summary} — `{result.fix.find}` not found in {target} (no-op)"
        )
        return False
    typer.echo(
        f"\nfix: {result.fix.summary} ({count} occurrence{'' if count == 1 else 's'} in {target})"
    )
    typer.echo(_triage.diff_fix(src, patched, target))
    if not write:
        typer.echo("dry-run — re-run with --write to apply.")
        return False
    Path(target).write_text(patched, encoding="utf-8")
    typer.echo(f"wrote {target}")
    return True


def _rerun_command(target: str, app_name: str, backend: str, udid: str, config: str) -> list[str]:
    """The `bajutsu run` invocation that re-checks a patched scenario (kept --no-erase to reuse
    the current device state). Built as a list so it is easy to assert in tests."""
    cmd = [
        sys.executable,
        "-m",
        "bajutsu",
        "run",
        "--scenario",
        target,
        "--app",
        app_name,
        "--config",
        config,
        "--no-erase",
    ]
    if backend:
        cmd += ["--backend", backend]
    if udid:
        cmd += ["--udid", udid]
    return cmd


def _verify_rerun(target: str, app_name: str, backend: str, udid: str, config: str) -> None:
    cmd = _rerun_command(target, app_name, backend, udid, config)
    typer.echo(f"\nre-running {target} to verify the fix ...")
    code = subprocess.call(cmd)
    typer.echo(
        "fix verified — the scenario now passes."
        if code == 0
        else "the scenario still fails after the fix; further diagnosis needed."
    )


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


@app.command()
def approve(
    run_dir: str = typer.Argument("", help="run directory (default: the latest under runs/)"),
    baselines: str = typer.Option(
        ..., "--baselines", help="baselines dir to promote the captured screenshots into"
    ),
    scenario: str = typer.Option(
        "", "--scenario", help="only this scenario id (e.g. 00-home), as in the run dir"
    ),
    all_: bool = typer.Option(
        False, "--all", help="also refresh baselines whose comparison already passed"
    ),
    runs: str = typer.Option("runs", help="runs root (used when run_dir is omitted)"),
) -> None:
    """Promote a run's captured screenshots to `visual` baselines.

    By default only failing / missing-baseline visual checks are approved; `--all` also
    refreshes baselines whose comparison passed. Reads the run's manifest.json, so it needs
    no Simulator — pair it with the WebUI's Approve button or use it headless in CI."""
    path = Path(run_dir) if run_dir else _trace.latest_run(Path(runs))
    if path is None or not (path / "manifest.json").is_file():
        typer.echo(f"no run found{f': {run_dir}' if run_dir else f' under {runs}/'}")
        raise typer.Exit(2)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    baselines_dir = Path(baselines)
    promoted = 0
    for scn in manifest.get("scenarios", []):
        for a in scn.get("expect_results", []):
            ev = a.get("visual")
            if a.get("kind") != "visual" or not ev or not ev.get("actual"):
                continue
            sid = str(ev["actual"]).split("/", 1)[0]
            if scenario and sid != scenario:
                continue
            if a.get("ok") and not all_:
                continue
            src = path / ev["actual"]
            if not src.is_file():
                typer.echo(f"skip {ev['baseline_name']}: missing {ev['actual']}")
                continue
            dest = baselines_dir / ev["baseline_name"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            typer.echo(f"approved {ev['baseline_name']}  ←  {sid}")
            promoted += 1
    if not promoted:
        typer.echo("nothing to approve (no failing visual checks; use --all to refresh)")
        raise typer.Exit(1)
    typer.echo(f"approved {promoted} baseline(s) → {baselines_dir}")


@app.command()
def serve(
    port: int = typer.Option(8765, "--port"),
    config: str = typer.Option(
        "", "--config", help="config to bind at startup; omit to open one from the UI"
    ),
    root: str = typer.Option(
        "", "--root", help="root the UI's file browser may explore (default: current directory)"
    ),
    scenarios: str = typer.Option(
        "", "--scenarios", help="override the app's scenarios dir (default: from config)"
    ),
    runs: str = typer.Option("runs", "--runs", help="runs root to serve reports from"),
    baselines: str = typer.Option(
        "",
        "--baselines",
        help="visual-regression baselines dir (default: a `baselines` folder under --scenarios)",
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
) -> None:
    """Launch a local web UI to run scenarios and view their reports (Tier 1; not for CI).

    Without `--config`, open a config.yml from the UI's file browser (limited to `--root`)."""
    from bajutsu.serve import serve as _serve

    _serve(
        host,
        port,
        Path(scenarios) if scenarios else None,
        Path(config) if config else None,
        Path(runs),
        Path(root) if root else Path.cwd(),
        Path(baselines) if baselines else None,
    )


if __name__ == "__main__":
    app()
