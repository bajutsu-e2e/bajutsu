"""Run pipeline — execute scenarios through a driver factory and write the report.

The driver factory encapsulates "launch the app for this scenario and return a
ready driver", so the runner stays backend-agnostic and testable with the fake
driver.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from bajutsu import env
from bajutsu.backends import default_available, make_driver, select_actuator
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import EvidenceSink
from bajutsu.orchestrator import BlockedHandler, Clock, RunResult, run_scenario
from bajutsu.report import write_report
from bajutsu.scenario import Preconditions, Scenario

DriverFactory = Callable[[Effective, Scenario], base.Driver]


def launch_driver(
    udid: str,
    eff: Effective,
    actuator: str,
    preconditions: Preconditions | None = None,
    env_run: env.RunFn = env._real_run,
) -> base.Driver:
    """Erase/boot/launch the app (with config + scenario env) and return a driver.

    The simctl sequencing is best-effort and should be confirmed on a real device.
    """
    pre = preconditions or Preconditions()
    e = env.Env(udid, run=env_run)
    if pre.erase:
        e.erase()
    e.boot()
    e.terminate(eff.bundle_id)  # clean start so readiness reflects the new launch
    launch_env: Mapping[str, str] = {**eff.launch_env, **pre.launch_env}
    e.launch(eff.bundle_id, [*eff.launch_args, *pre.launch_args], launch_env)
    if pre.deeplink is not None:
        e.openurl(pre.deeplink)
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
) -> list[RunResult]:
    """Run every scenario, each with a freshly built driver."""
    return [
        run_scenario(factory(eff, s), s, clock, sink=sink, on_blocked=on_blocked)
        for s in scenarios
    ]


def run_and_report(
    eff: Effective,
    scenarios: list[Scenario],
    factory: DriverFactory,
    runs_dir: Path,
    run_id: str,
    clock: Clock | None = None,
    on_blocked: BlockedHandler | None = None,
    sink: EvidenceSink | None = None,
) -> tuple[list[RunResult], Path]:
    """Run scenarios and write manifest.json + JUnit under runs_dir/run_id."""
    results = run_all(eff, scenarios, factory, clock, on_blocked=on_blocked, sink=sink)
    manifest = write_report(runs_dir / run_id, run_id, results)
    return results, manifest


def device_factory(
    udid: str,
    backends: list[str],
    available: Callable[[str], bool] = default_available,
    env_run: env.RunFn = env._real_run,
) -> DriverFactory:
    """A real driver factory: pick the actuator, then launch the app per scenario.

    The simctl sequencing (erase/boot) is best-effort and should be confirmed on a
    real device.
    """
    actuator = select_actuator(backends, available)

    def factory(eff: Effective, scenario: Scenario) -> base.Driver:
        return launch_driver(udid, eff, actuator, scenario.preconditions, env_run)

    return factory
