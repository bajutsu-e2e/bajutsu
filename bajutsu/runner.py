"""Run pipeline — execute scenarios through a driver factory and write the report.

The driver factory encapsulates "launch the app for this scenario and return a
ready driver", so the runner stays backend-agnostic and testable with the fake
driver.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from bajutsu import env
from bajutsu.backends import default_available, make_driver, select_actuator
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import Artifact, EvidenceSink
from bajutsu.network import NetworkCollector, NetworkExchange
from bajutsu.orchestrator import (
    BlockedHandler,
    Clock,
    DeviceControl,
    RelaunchFn,
    RunResult,
    run_scenario,
    scenario_slug,
)
from bajutsu.redaction import Redactor
from bajutsu.report import write_report
from bajutsu.scenario import Preconditions, Relaunch, Scenario, dump_scenarios, scenario_dict

DriverFactory = Callable[[Effective, Scenario], base.Driver]
# Run after each scenario finishes (e.g. terminate the app -> back to SpringBoard).
Teardown = Callable[[Effective, Scenario], None]
# Builds the in-scenario relaunch function for a scenario (given its live driver).
RelaunchFactory = Callable[[Effective, Scenario, base.Driver], RelaunchFn]


def _no_net() -> list[NetworkExchange]:
    return []


def _write_network(
    timed: list[tuple[NetworkExchange, float]], scenario_start: float,
    run_dir: Path, sid: str, redactor: Redactor,
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
        d["startedAt"] = round(max(0.0, received - scenario_start - (ex.duration_ms or 0.0) / 1000.0), 3)
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
) -> base.Driver:
    """Erase/boot/launch the app (with config + scenario env) and return a driver.

    simctl `erase` requires a shut-down device, so an erase run shuts the device
    down first (shutdown -> erase -> boot); otherwise erasing a booted Simulator
    fails. Any simctl step that still fails (e.g. the app isn't installed) is
    surfaced as a clean env.DeviceError so the CLI can exit 2 instead of dumping a
    traceback.
    """
    pre = preconditions or Preconditions()
    e = env.Env(udid, run=env_run)
    try:
        if pre.erase:
            e.shutdown()  # erase only works on a shut-down device
            e.erase()
        e.boot()
        e.terminate(eff.bundle_id)  # clean start so readiness reflects the new launch
        launch_env: Mapping[str, str] = {**eff.launch_env, **pre.launch_env}
        locale = pre.locale or eff.locale  # scenario locale overrides the app/config default
        e.launch(
            eff.bundle_id, [*eff.launch_args, *pre.launch_args, *env.locale_args(locale)], launch_env
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
        except Exception:  # noqa: BLE001 — app may not be answerable yet
            pass
        time.sleep(poll)


def run_all(
    eff: Effective,
    scenarios: list[Scenario],
    factory: DriverFactory,
    clock: Clock | None = None,
    on_blocked: BlockedHandler | None = None,
    sink: EvidenceSink | None = None,
    teardown: Teardown | None = None,
    collector: NetworkCollector | None = None,
    run_dir: Path | None = None,
    relauncher: RelaunchFactory | None = None,
    workers: int = 1,
    release: Callable[[base.Driver], None] | None = None,
    bindings: Mapping[str, str] | None = None,
    secret_values: list[str] | None = None,
    control: DeviceControl | None = None,
) -> list[RunResult]:
    """Run every scenario, each with a freshly built driver.

    After each scenario finishes, `teardown` runs (e.g. terminate the app so the
    Simulator returns to SpringBoard between scenarios and after the last one) and, if
    given, `release(driver)` returns the scenario's device to a pool. When a `collector`
    is given, its exchanges are cleared per scenario, exposed to `request` assertions, and
    written to <sid>/network.json.

    With `workers > 1` scenarios run concurrently (results stay in declaration order). The
    caller must supply device-independent resources per scenario — a device-pool `factory`
    / `release` and no shared `collector` (the single loopback receiver is not
    parallel-safe).
    """
    if workers > 1 and collector is not None:
        raise ValueError("並列実行（workers>1）は共有コレクタ非対応。--no-network で実行")
    redactor = Redactor(eff.redact, values=secret_values)

    def run_one(i: int, s: Scenario) -> RunResult:
        sid = f"{i:02d}-{scenario_slug(s.name)}"
        if collector is not None:
            collector.clear()
        driver = factory(eff, s)
        try:
            # t0 after launch, so exchange offsets share the step timeline's origin.
            scenario_start = time.monotonic()
            result = run_scenario(
                driver, s, clock, sink=sink, on_blocked=on_blocked, scenario_id=sid,
                network=(collector.snapshot if collector is not None else _no_net),
                relaunch=(relauncher(eff, s, driver) if relauncher is not None else None),
                bindings=bindings, control=control,
            )
            if teardown is not None:
                teardown(eff, s)
            if collector is not None and run_dir is not None:
                art = _write_network(collector.snapshot_timed(), scenario_start, run_dir, sid, redactor)
                if art is not None:
                    result.artifacts.append(art)
            return result
        finally:
            if release is not None:
                release(driver)

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda pair: run_one(*pair), list(enumerate(scenarios))))
    return [run_one(i, s) for i, s in enumerate(scenarios)]


def run_and_report(
    eff: Effective,
    scenarios: list[Scenario],
    factory: DriverFactory,
    runs_dir: Path,
    run_id: str,
    clock: Clock | None = None,
    on_blocked: BlockedHandler | None = None,
    sink: EvidenceSink | None = None,
    teardown: Teardown | None = None,
    collector: NetworkCollector | None = None,
    relauncher: RelaunchFactory | None = None,
    workers: int = 1,
    release: Callable[[base.Driver], None] | None = None,
    bindings: Mapping[str, str] | None = None,
    secret_values: list[str] | None = None,
    control: DeviceControl | None = None,
    source_name: str | None = None,
) -> tuple[list[RunResult], Path]:
    """Run scenarios and write manifest.json + JUnit + scenario.yaml under runs_dir/run_id."""
    run_dir = runs_dir / run_id
    results = run_all(
        eff, scenarios, factory, clock, on_blocked=on_blocked, sink=sink, teardown=teardown,
        collector=collector, run_dir=run_dir, relauncher=relauncher, workers=workers, release=release,
        bindings=bindings, secret_values=secret_values, control=control,
    )
    # The merged Result tab renders each scenario as a structured view (definitions)
    # with a toggle to the raw YAML (sources).
    definitions = [scenario_dict(s) for s in scenarios]
    sources = [dump_scenarios([s]) for s in scenarios]
    run_dir.mkdir(parents=True, exist_ok=True)
    # Keep the executed scenario alongside its results (re-runnable / reviewable).
    (run_dir / "scenario.yaml").write_text(dump_scenarios(scenarios), encoding="utf-8")
    manifest = write_report(run_dir, run_id, results, definitions, sources, source_name)
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


def device_factory(
    udid: str,
    backends: list[str],
    available: Callable[[str], bool] = default_available,
    env_run: env.RunFn = env._real_run,
) -> DriverFactory:
    """A real driver factory: pick the actuator, then launch the app per scenario.

    The simctl sequencing (shutdown/erase/boot) lives in launch_driver, which
    surfaces any failure as a clean env.DeviceError.
    """
    actuator = select_actuator(backends, available)

    def factory(eff: Effective, scenario: Scenario) -> base.Driver:
        return launch_driver(udid, eff, actuator, scenario.preconditions, env_run)

    return factory


def device_teardown(udid: str, env_run: env.RunFn = env._real_run) -> Teardown:
    """A teardown that terminates the app after each scenario, returning the
    Simulator to SpringBoard."""
    e = env.Env(udid, run=env_run)

    def teardown(eff: Effective, scenario: Scenario) -> None:
        e.terminate(eff.bundle_id)

    return teardown


def device_pool(
    udids: list[str],
    backends: list[str],
    bundle_id: str,
    available: Callable[[str], bool] = default_available,
    env_run: env.RunFn = env._real_run,
) -> tuple[DriverFactory, RelaunchFactory, Callable[[base.Driver], None]]:
    """A pool of devices for parallel runs. The factory leases a free udid per scenario
    (blocking until one frees up), and release() terminates that scenario's app and returns
    its udid to the pool. relauncher/release act on the leased device. Returns
    (factory, relauncher, release)."""
    actuator = select_actuator(backends, available)
    free: queue.Queue[str] = queue.Queue()
    for udid in udids:
        free.put(udid)
    leased: dict[int, str] = {}
    lock = threading.Lock()

    def factory(eff: Effective, scenario: Scenario) -> base.Driver:
        udid = free.get()
        driver = launch_driver(udid, eff, actuator, scenario.preconditions, env_run)
        with lock:
            leased[id(driver)] = udid
        return driver

    def relauncher(eff: Effective, scenario: Scenario, driver: base.Driver) -> RelaunchFn:
        with lock:
            udid = leased[id(driver)]
        return device_relauncher(udid, env_run)(eff, scenario, driver)

    def release(driver: base.Driver) -> None:
        with lock:
            udid = leased.pop(id(driver), None)
        if udid is not None:
            env.Env(udid, run=env_run).terminate(bundle_id)
            free.put(udid)

    return factory, relauncher, release


def device_control(
    udid: str, bundle_id: str, env_run: env.RunFn = env._real_run
) -> DeviceControl:
    """A DeviceControl bound to one device, backing `setLocation` / `push` steps via
    simctl. Not used in parallel runs (no single pinned device)."""
    e = env.Env(udid, run=env_run)

    class _Control:
        def set_location(self, lat: float, lon: float) -> None:
            e.set_location(lat, lon)

        def push(self, payload: dict[str, object]) -> None:
            e.push(bundle_id, payload)

    return _Control()


def device_relauncher(udid: str, env_run: env.RunFn = env._real_run) -> RelaunchFactory:
    """A relauncher for a `relaunch` step: terminate the app and launch it again (re-applying
    the scenario's launch env/args, plus any per-relaunch overrides), then wait until ready.
    The device is not erased/rebooted — only the app process restarts."""
    e = env.Env(udid, run=env_run)

    def for_scenario(eff: Effective, scenario: Scenario, driver: base.Driver) -> RelaunchFn:
        pre = scenario.preconditions

        def relaunch(opts: Relaunch) -> None:
            e.terminate(eff.bundle_id)
            launch_env = {**eff.launch_env, **pre.launch_env, **(opts.env or {})}
            locale = pre.locale or eff.locale
            launch_args = [
                *eff.launch_args, *pre.launch_args, *(opts.args or []), *env.locale_args(locale)
            ]
            e.launch(eff.bundle_id, launch_args, launch_env)
            _await_ready(driver)

        return relaunch

    return for_scenario
