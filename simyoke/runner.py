"""Run pipeline — execute scenarios through a driver factory and write the report.

The driver factory encapsulates "launch the app for this scenario and return a
ready driver", so the runner stays backend-agnostic and testable with the fake
driver.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from simyoke import env
from simyoke.backends import default_available, make_driver, select_actuator
from simyoke.config import Effective
from simyoke.drivers import base
from simyoke.orchestrator import Clock, RunResult, run_scenario
from simyoke.report import write_report
from simyoke.scenario import Preconditions, Scenario

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
    launch_env: Mapping[str, str] = {**eff.launch_env, **pre.launch_env}
    e.launch(eff.bundle_id, [*eff.launch_args, *pre.launch_args], launch_env)
    if pre.deeplink is not None:
        e.openurl(pre.deeplink)
    return make_driver(actuator, udid)


def run_all(
    eff: Effective,
    scenarios: list[Scenario],
    factory: DriverFactory,
    clock: Clock | None = None,
) -> list[RunResult]:
    """Run every scenario, each with a freshly built driver."""
    return [run_scenario(factory(eff, s), s, clock) for s in scenarios]


def run_and_report(
    eff: Effective,
    scenarios: list[Scenario],
    factory: DriverFactory,
    runs_dir: Path,
    run_id: str,
    clock: Clock | None = None,
) -> tuple[list[RunResult], Path]:
    """Run scenarios and write manifest.json + JUnit under runs_dir/run_id."""
    results = run_all(eff, scenarios, factory, clock)
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
