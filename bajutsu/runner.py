"""Run pipeline — execute scenarios through a device pool and write the report.

The pool leases a device per scenario: it launches the app and returns a `Lease`
bundling the live driver with that device's per-device resources (evidence sink,
relaunch, device control, network collector). A single-device run is just a pool of
one, so network collection / interval evidence / device control work the same whether
`workers` is 1 or N. The run loop stays backend-agnostic and testable with a fake
lease over the fake driver.
"""

from __future__ import annotations

import json
import queue
import subprocess
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bajutsu import env
from bajutsu.assertions import VisualContext
from bajutsu.backends import default_available, make_driver, select_actuator
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import Artifact, EvidenceSink, FileSink
from bajutsu.network import NetworkCollector, NetworkExchange
from bajutsu.orchestrator import (
    BlockedHandler,
    Clock,
    DeviceControl,
    ProgressFn,
    RelaunchFn,
    RunResult,
    run_scenario,
    scenario_slug,
)
from bajutsu.redaction import Redactor
from bajutsu.report import write_report
from bajutsu.scenario import (
    Preconditions,
    Relaunch,
    Scenario,
    dump_scenario_file,
    dump_scenarios,
    scenario_dict,
)

# Builds the in-scenario relaunch function for a scenario (given its live driver).
RelaunchFactory = Callable[[Effective, Scenario, base.Driver], RelaunchFn]

# Selects the alert-guard handler for one scenario (None = no guard for it). The CLI sets this
# so each scenario's `dismissAlerts` (default on, optional instruction) decides whether — and
# how — the vision guard runs; the orchestrator stays oblivious to the per-scenario choice.
OnBlockedFor = Callable[[Scenario], "BlockedHandler | None"]


@dataclass
class Lease:
    """A leased device for one scenario run: its live driver plus the per-device
    resources bound to that device. `release()` terminates the app and returns the
    device to the pool.

    `collector` is None when network collection is off; otherwise it is the device's
    own receiver (the app POSTs to it), cleared per scenario by the run loop.
    """

    driver: base.Driver
    sink: EvidenceSink
    relaunch: RelaunchFn | None
    control: DeviceControl | None
    collector: NetworkCollector | None
    release: Callable[[], None]
    udid: str = ""  # the leased device, recorded on each RunResult (parallel-split attribution)
    # The leased device's model / OS runtime, recorded on the result for the report's
    # Environment tab (empty when the simulator catalog couldn't be read).
    device_name: str = ""
    device_runtime: str = ""


# Leases a free device for one scenario (blocking until one frees up): launches the app
# and returns the Lease the run loop drives, then release()s.
LeaseFn = Callable[[Effective, Scenario], Lease]


def _no_net() -> list[NetworkExchange]:
    return []


def _write_network(
    timed: list[tuple[NetworkExchange, float]],
    scenario_start: float,
    run_dir: Path,
    sid: str,
    redactor: Redactor,
) -> Artifact | None:
    """Write a scenario's observed exchanges to <sid>/network.json (redacted).

    Each exchange gets a `startedAt` offset (seconds from the scenario's start, the same
    frame as a step's `started_at`) so the report can place it on the timeline: the
    receive time is ≈ completion, so the start is `received - scenario_start - duration`.
    """
    if not timed:
        return None
    data: list[dict[str, Any]] = []
    for ex, received in timed:
        d = ex.model_dump(by_alias=True, exclude_none=True)
        d["startedAt"] = round(
            max(0.0, received - scenario_start - (ex.duration_ms or 0.0) / 1000.0), 3
        )
        data.append(redactor.redact_exchange(d))
    text = json.dumps(data, ensure_ascii=False, indent=2)
    out = run_dir / sid / "network.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return Artifact(f"{sid}/network.json", "network", "collector")


def launch_driver(
    udid: str,
    eff: Effective,
    actuator: str,
    preconditions: Preconditions | None = None,
    env_run: env.RunFn = env._real_run,
    extra_env: Mapping[str, str] | None = None,
) -> base.Driver:
    """Erase/boot/launch the app (with config + scenario env) and return a driver.

    simctl `erase` requires a shut-down device, so an erase run shuts the device
    down first (shutdown -> erase -> boot); otherwise erasing a booted Simulator
    fails. Any simctl step that still fails (e.g. the app isn't installed) is
    surfaced as a clean env.DeviceError so the CLI can exit 2 instead of dumping a
    traceback.

    `extra_env` is merged last into the launch env (e.g. the per-device
    `BAJUTSU_COLLECTOR` url so the app reports to its own collector).
    """
    pre = preconditions or Preconditions()
    e = env.Env(udid, run=env_run)
    try:
        if pre.erase:
            e.shutdown()  # erase only works on a shut-down device
            e.erase()
        e.boot()
        # When the app config gives a built .app, reinstall it before each run so every
        # scenario starts from a known-good binary. `reinstall=clean` (default) uninstalls
        # first (fresh app + data); `overwrite` installs over the existing app (keeps its
        # data). After an `erase` the app is already gone, so the uninstall is skipped.
        if eff.app_path:
            if not Path(eff.app_path).exists():
                raise env.DeviceError(f"appPath not found: {eff.app_path} (build the app first)")
            if pre.reinstall == "clean" and not pre.erase:
                e.uninstall(eff.bundle_id)
            e.install(eff.app_path)
        e.terminate(eff.bundle_id)  # clean start so readiness reflects the new launch
        launch_env: Mapping[str, str] = {**eff.launch_env, **pre.launch_env, **(extra_env or {})}
        locale = pre.locale or eff.locale  # scenario locale overrides the app/config default
        e.launch(
            eff.bundle_id,
            [*eff.launch_args, *pre.launch_args, *env.locale_args(locale)],
            launch_env,
        )
        if pre.deeplink is not None:
            e.openurl(pre.deeplink)
    except subprocess.CalledProcessError as exc:
        raise env.device_error(exc) from exc
    driver = make_driver(actuator, udid)
    _await_ready(driver)
    return driver


def _await_ready(driver: base.Driver, timeout: float = 10.0, poll: float = 0.2) -> None:
    """Poll until the launched app has rendered a UI (more than the app root element)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if len(driver.query()) >= 2:
                return
        except (OSError, subprocess.CalledProcessError, ValueError):
            pass
        time.sleep(poll)


def run_all(
    eff: Effective,
    scenarios: list[Scenario],
    lease: LeaseFn,
    clock: Clock | None = None,
    on_blocked: BlockedHandler | None = None,
    on_blocked_for: OnBlockedFor | None = None,
    run_dir: Path | None = None,
    workers: int = 1,
    bindings: Mapping[str, str] | None = None,
    secret_values: list[str] | None = None,
    progress: ProgressFn | None = None,
    baselines_dir: Path | None = None,
) -> list[RunResult]:
    """Run every scenario, each on a freshly leased device.

    `lease(eff, scenario)` blocks until a device is free, launches the app, and returns a
    Lease bundling the live driver with that device's evidence sink / relaunch / control /
    network collector. After the scenario finishes, `lease.release()` terminates the app and
    returns the device to the pool. When the lease carries a collector, its exchanges are
    cleared per scenario, exposed to `request` assertions, and written to <sid>/network.json
    (redacted with `secret_values`).

    `on_blocked_for`, when given, picks each scenario's alert-guard handler (honoring its
    `dismissAlerts`); it takes precedence over the single `on_blocked` (used by tests).

    With `workers > 1` scenarios run concurrently (results stay in declaration order). The
    pool hands each worker its own device and per-device resources, so the run loop has no
    shared mutable state.
    """
    redactor = Redactor(eff.redact, values=secret_values)

    total = len(scenarios)

    def run_one(i: int, s: Scenario) -> RunResult:
        sid = f"{i:02d}-{scenario_slug(s.name)}"
        if progress is not None:
            progress(f"▶ scenario {i + 1}/{total}: {s.name}")
        lz = lease(eff, s)
        handler = on_blocked_for(s) if on_blocked_for is not None else on_blocked
        try:
            if lz.collector is not None:
                lz.collector.clear()
            # t0 after launch, so exchange offsets share the step timeline's origin.
            scenario_start = time.monotonic()
            # Build visual context for scenario-level visual assertions (expect).
            vc: VisualContext | None = None
            if baselines_dir is not None and run_dir is not None:
                vc = VisualContext(
                    screenshot_path=run_dir / sid / "visual-actual.png",
                    baselines_dir=baselines_dir,
                    diff_dir=run_dir / sid,
                    run_dir=run_dir,
                )
            result = run_scenario(
                lz.driver,
                s,
                clock,
                sink=lz.sink,
                on_blocked=handler,
                scenario_id=sid,
                network=(lz.collector.snapshot if lz.collector is not None else _no_net),
                relaunch=lz.relaunch,
                bindings=bindings,
                control=lz.control,
                progress=progress,
                visual_context=vc,
            )
            result.device = lz.udid  # attribute the scenario to the device that ran it
            result.device_name = lz.device_name  # for the report's Environment tab
            result.device_runtime = lz.device_runtime
            if lz.collector is not None and run_dir is not None:
                art = _write_network(
                    lz.collector.snapshot_timed(), scenario_start, run_dir, sid, redactor
                )
                if art is not None:
                    result.artifacts.append(art)
            if progress is not None:
                mark = "✔" if result.ok else "✘"
                progress(f"{mark} scenario {i + 1}/{total}: {s.name} ({result.duration_s:.1f}s)")
            return result
        finally:
            lz.release()

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda pair: run_one(*pair), list(enumerate(scenarios))))
    return [run_one(i, s) for i, s in enumerate(scenarios)]


def run_and_report(
    eff: Effective,
    scenarios: list[Scenario],
    lease: LeaseFn,
    runs_dir: Path,
    run_id: str,
    clock: Clock | None = None,
    on_blocked: BlockedHandler | None = None,
    on_blocked_for: OnBlockedFor | None = None,
    workers: int = 1,
    bindings: Mapping[str, str] | None = None,
    secret_values: list[str] | None = None,
    source_name: str | None = None,
    description: str | None = None,
    progress: ProgressFn | None = None,
    baselines_dir: Path | None = None,
) -> tuple[list[RunResult], Path]:
    """Run scenarios and write manifest.json + JUnit + scenario.yaml under runs_dir/run_id.

    When `baselines_dir` is given, `visual` assertions compare each scenario's end-state
    screenshot against a baseline image in that directory (see run_all)."""
    run_dir = runs_dir / run_id
    results = run_all(
        eff,
        scenarios,
        lease,
        clock,
        on_blocked=on_blocked,
        on_blocked_for=on_blocked_for,
        run_dir=run_dir,
        workers=workers,
        bindings=bindings,
        secret_values=secret_values,
        progress=progress,
        baselines_dir=baselines_dir,
    )
    # The merged Result tab renders each scenario as a structured view (definitions)
    # with a toggle to the raw YAML (sources).
    definitions = [scenario_dict(s) for s in scenarios]
    sources = [dump_scenarios([s]) for s in scenarios]
    run_dir.mkdir(parents=True, exist_ok=True)
    # Keep the executed scenario alongside its results (re-runnable / reviewable).
    (run_dir / "scenario.yaml").write_text(
        dump_scenario_file(scenarios, description), encoding="utf-8"
    )
    manifest = write_report(
        run_dir, run_id, results, definitions, sources, source_name, description
    )
    # Final safety net: scrub any literal secret value that reached a run-level artifact
    # (e.g. an assertion's expected/actual text in the manifest / HTML). The scenario
    # definitions already hold tokens, not values, so this only catches result text.
    _scrub_secret_values(run_dir, secret_values)
    return results, manifest


def _scrub_secret_values(run_dir: Path, secret_values: list[str] | None) -> None:
    if not secret_values:
        return
    scrub = Redactor(None, values=secret_values)
    for name in ("manifest.json", "junit.xml", "report.html", "scenario.yaml"):
        path = run_dir / name
        if path.exists():
            path.write_text(scrub.redact_text(path.read_text(encoding="utf-8")), encoding="utf-8")


def device_pool(
    udids: list[str],
    backends: list[str],
    eff: Effective,
    run_dir: Path,
    *,
    network: bool = False,
    log_predicate: str | None = None,
    log_subsystem: str | None = None,
    secret_values: list[str] | None = None,
    available: Callable[[str], bool] = default_available,
    env_run: env.RunFn = env._real_run,
) -> tuple[LeaseFn, Callable[[], None]]:
    """A pool of N>=1 devices for (parallel) runs.

    `lease(eff, scenario)` leases a free udid (blocking until one frees up), launches the app
    pointed at that device's own network collector, and returns a Lease whose evidence sink
    (interval recordings under `run_dir`), relaunch, and device control are all bound to the
    leased device. The Lease's `release()` terminates the app and returns the udid to the
    pool. `shutdown()` stops every device's collector.

    A single-device run is just a pool of one, so network collection / interval evidence /
    device control work the same whether `workers` is 1 or N. The only shared state is the
    free-device queue (thread-safe) and the read-only collectors map, so leases need no lock.

    Returns (lease, shutdown).
    """
    actuator = select_actuator(backends, available)
    # Resolve the device model / OS once up front (static per device) so each result can name
    # the simulator it ran on in the report; best-effort, so a missing catalog just omits it.
    catalog = env.device_catalog(env_run)
    free: queue.Queue[str] = queue.Queue()
    for udid in udids:
        free.put(udid)
    # One collector per device (its own ephemeral port), started up front and reused across
    # leases (cleared per scenario by the run loop). If a start fails mid-setup, stop the
    # ones already started so we don't leak listening sockets.
    collectors: dict[str, NetworkCollector] = {}
    if network:
        started: list[NetworkCollector] = []
        try:
            for udid in udids:
                collector = NetworkCollector()
                collector.start()
                collectors[udid] = collector
                started.append(collector)
        except Exception:
            for collector in started:
                collector.stop()
            raise

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        udid = free.get()
        collector = collectors.get(udid)
        # Point the app at this device's collector (survives a relaunch via the relauncher).
        extra_env = (
            {"BAJUTSU_COLLECTOR": f"http://127.0.0.1:{collector.port}"}
            if collector is not None
            else None
        )
        driver = launch_driver(udid, eff, actuator, scenario.preconditions, env_run, extra_env)
        sink = FileSink(
            run_dir,
            udid=udid,
            log_predicate=log_predicate,
            log_subsystem=log_subsystem,
            redact=eff.redact,
            secrets=secret_values,
        )
        relaunch = device_relauncher(udid, env_run, extra_env)(eff, scenario, driver)
        control = device_control(udid, eff.bundle_id, env_run)

        def release() -> None:
            env.Env(udid, run=env_run).terminate(eff.bundle_id)
            free.put(udid)

        meta = catalog.get(udid, {})
        return Lease(
            driver=driver,
            sink=sink,
            relaunch=relaunch,
            control=control,
            collector=collector,
            release=release,
            udid=udid,
            device_name=meta.get("name", ""),
            device_runtime=meta.get("runtime", ""),
        )

    def shutdown() -> None:
        for collector in collectors.values():
            collector.stop()

    return lease, shutdown


def device_control(udid: str, bundle_id: str, env_run: env.RunFn = env._real_run) -> DeviceControl:
    """A DeviceControl bound to one device, backing `setLocation` / `push` /
    `background` / `overrideStatusBar` / `clearStatusBar` steps via simctl."""
    e = env.Env(udid, run=env_run)

    class _Control:
        def set_location(self, lat: float, lon: float) -> None:
            e.set_location(lat, lon)

        def push(self, payload: dict[str, object]) -> None:
            e.push(bundle_id, payload)

        def home(self) -> None:
            e.home()

        def override_status_bar(self, **kwargs: str | int) -> None:
            e.override_status_bar(**kwargs)

        def clear_status_bar(self) -> None:
            e.clear_status_bar()

    return _Control()


def device_relauncher(
    udid: str, env_run: env.RunFn = env._real_run, extra_env: Mapping[str, str] | None = None
) -> RelaunchFactory:
    """A relauncher for a `relaunch` step: terminate the app and launch it again (re-applying
    the scenario's launch env/args, plus any per-relaunch overrides), then wait until ready.
    The device is not erased/rebooted — only the app process restarts.

    `extra_env` (e.g. the device's collector url) is re-applied so it survives the relaunch;
    an explicit per-relaunch `env` override still wins over it.
    """
    e = env.Env(udid, run=env_run)

    def for_scenario(eff: Effective, scenario: Scenario, driver: base.Driver) -> RelaunchFn:
        pre = scenario.preconditions

        def relaunch(opts: Relaunch) -> None:
            e.terminate(eff.bundle_id)
            launch_env = {
                **eff.launch_env,
                **pre.launch_env,
                **(extra_env or {}),
                **(opts.env or {}),
            }
            locale = pre.locale or eff.locale
            launch_args = [
                *eff.launch_args,
                *pre.launch_args,
                *(opts.args or []),
                *env.locale_args(locale),
            ]
            e.launch(eff.bundle_id, launch_args, launch_env)
            _await_ready(driver)

        return relaunch

    return for_scenario
