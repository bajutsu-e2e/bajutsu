"""`bajutsu run` — execute a scenario deterministically (the Tier-2 CI gate)."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import typer

from bajutsu import env as _env
from bajutsu import github
from bajutsu import usage as _usage
from bajutsu.anthropic_client import credential_gap
from bajutsu.backends import ensure_web_runtime, select_actuator
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective
from bajutsu.config import Effective
from bajutsu.report.archive import archive_run_dir
from bajutsu.runner import device_pool, run_and_report
from bajutsu.runner.launch_server import start_launch_server
from bajutsu.scenario import (
    DismissAlerts,
    Scenario,
    apply_setups,
    dump_mocks,
    expand_components,
    expand_data,
    load_component,
    load_scenario_file,
    load_scenarios,
    read_csv,
    select_scenarios,
)


def _resolve_baselines_dir(flag: str, eff: Effective, scenario_file: Path) -> Path:
    """Resolve the baseline images directory for visual assertions.

    Resolution order (highest to lowest priority):
    1. --baselines flag: explicit path on the command line
    2. config baselines: apps.<name>.baselines in the config file
    3. scenario-local default: baselines/ beside the scenario file
    """
    # flag > config > scenario-local default
    if flag:
        return Path(flag)
    elif eff.baselines:
        return Path(eff.baselines)
    else:
        return scenario_file.parent / "baselines"


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


def run(
    app_name: str = typer.Option(..., "--app"),
    scenario: str = typer.Option(
        "",
        "--scenario",
        help="run only this one *.yaml (overrides the app's configured scenarios dir)",
    ),
    backend: str = typer.Option(
        "",
        help="comma list of platforms (ios/android/web/fake) or actuators (idb); first available wins",
    ),
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
        help="override every scenario's dismissAlerts (default: per-scenario, on; needs the "
        "configured AI provider — ANTHROPIC_API_KEY, or AWS credentials for Bedrock)",
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
        help="collect the app's network exchanges (for `request` assertions); iOS needs BajutsuKit "
        "in the app, web (Playwright) observes natively",
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
        "(default: config baselines, then baselines/ beside the scenario)",
    ),
    headed: bool | None = typer.Option(
        None,
        "--headed/--no-headed",
        help="web backend: show the browser (headed, slow-motion) instead of headless; "
        "default leaves the app's `headless` config (headless)",
    ),
    zip_run: bool = typer.Option(
        False,
        "--zip",
        help="after the run, also write runs/<id>.zip — one portable artifact (report + evidence) "
        "for CI upload or sharing; runs after the verdict, so it can't affect pass/fail",
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Run a scenario deterministically. Pass/fail is machine-only; the sole AI is the
    alert guard (on by default per scenario), which only fires to clear an OS prompt that
    blocked a step — see each scenario's `dismissAlerts`."""
    eff = _load_effective(config, app_name)
    # --headed/--no-headed overrides the app's `headless` config (web backend only; iOS ignores it).
    if headed is not None:
        eff = replace(eff, headless=not headed)
    before = _usage.snapshot()
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
        ensure_web_runtime(backends)  # auto-install Playwright if a web run needs it
        actuator = select_actuator(backends)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    if actuator == "playwright":
        # Web has no simctl udid to resolve: one browser lane, keyed by a dummy udid. Network
        # collection is native (Playwright observes the page), so `--network` works here too
        # (BE-0054) — the pool builds a page-hooked collector instead of an HTTP receiver.
        udids = ["web"]
        workers = 1
    else:
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

        # The vision guard reaches Claude through the configured AI provider (BE-0053), so the
        # credential it needs is provider-specific: ANTHROPIC_API_KEY for Anthropic, a
        # provider-prefixed BAJUTSU_BEDROCK_MODEL for Bedrock (AWS credentials authenticate there).
        guard_gap = credential_gap()
        if guard_gap == "anthropic-key":
            typer.echo(
                "note: dismiss-alerts is on but ANTHROPIC_API_KEY is unset — the alert guard will no-op"
            )
        elif guard_gap == "bedrock-model":
            typer.echo(
                "note: dismiss-alerts is on but BAJUTSU_BEDROCK_MODEL is unset — "
                "the alert guard will no-op"
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
    # Visual assertions resolve `baseline: <name>` within this directory.
    # Resolution order: --baselines flag > config baselines > baselines/ beside the scenario.
    baselines_dir = _resolve_baselines_dir(baselines, eff, files[0])
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
    # Bring up the app's target server (the web baseUrl host) if it declares `launchServer`, waiting
    # on its readiness probe; reused if already serving, torn down in the finally below. The pool
    # leases lazily (the web driver navigates at lease time, inside run_and_report), so the server
    # only needs to be up before the run, not before the pool.
    try:
        stop_server = start_launch_server(eff)
    except RuntimeError as e:
        typer.echo(str(e))
        shutdown()
        raise typer.Exit(2) from None
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
        stop_server()
    ok = all(r.ok for r in results)
    github.emit(results, manifest.parent / "report.html")  # annotations + summary in CI
    typer.echo(f"{'PASS' if ok else 'FAIL'}  {manifest}")
    # --zip packages the finished run into one artifact, strictly *after* the verdict above, so it
    # cannot influence pass/fail (BE-0060). Path goes to stderr; stdout stays the PASS/FAIL line.
    if zip_run:
        run_dir = manifest.parent
        zip_path = run_dir.parent / f"{run_dir.name}.zip"
        zip_path.write_bytes(archive_run_dir(run_dir))
        typer.echo(f"wrote {zip_path}", err=True)
    # The only AI in `run` is the alert guard (when it actually fired). Report its token use on
    # stderr so stdout stays the machine-readable PASS/FAIL line; silent when nothing fired.
    spent = _usage.snapshot() - before
    if spent.calls:
        typer.echo(spent.render(), err=True)
    raise typer.Exit(0 if ok else 1)


def register(app: typer.Typer) -> None:
    app.command()(run)
